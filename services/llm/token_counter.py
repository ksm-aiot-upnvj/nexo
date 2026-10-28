import asyncio
import hashlib
import logging
from collections import OrderedDict
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp
import tiktoken
from openai import AsyncOpenAI
from transformers import AutoTokenizer

from config.settings import settings

logger = logging.getLogger("token_counter")

LLM_BASE_URL = settings.LLM_BASE_URL
LLM_API_KEY = settings.LLM_API_KEY.strip("\"' \r\n")
HF_TOKEN = settings.HF_TOKEN.strip("\"' \r\n")
TOTAL_CONTEXT_WINDOW = settings.TOTAL_CONTEXT_WINDOW
LLM_MODEL_NAME = settings.LLM_MODEL_NAME
ai_client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY or "sk-no-key",
)

MAX_COMPLETION_TOKENS = 1024
BASE_OVERHEAD_TOKENS = 1400
TOOL_BUFFER_TOKENS = 1200

USABLE_HISTORY_BUDGET = max(
    1000,
    TOTAL_CONTEXT_WINDOW
    - (MAX_COMPLETION_TOKENS + BASE_OVERHEAD_TOKENS + TOOL_BUFFER_TOKENS),
)
COMPACTION_THRESHOLD = int(USABLE_HISTORY_BUDGET)
TARGET_RETAIN_TOKENS = int(USABLE_HISTORY_BUDGET * 0.40)

_TOKEN_CACHE: OrderedDict[str, int] = OrderedDict()
_MAX_CACHE_SIZE = 1000
_INFLIGHT_TOKEN_REQUESTS: dict[str, asyncio.Future[int]] = {}
_TOKENIZERS: dict[str, TokenCounter] = {}
_TOKENIZER_LOAD_ERRORS: set[str] = set()
_RESOLVED_TOKENIZER_MODEL: str | None = None
_MODEL_RESOLUTION_LOCK = asyncio.Lock()


class TokenCounter:
    """Local tokenizer wrapper for OpenAI (tiktoken) and Hugging Face models."""

    def __init__(self, model_name: str):
        self.model_name = model_name

        if self._looks_like_openai(model_name):
            self.backend = "tiktoken"
            try:
                self.tokenizer = tiktoken.encoding_for_model(model_name)
            except KeyError:
                # Fallbacks when the model is not registered with tiktoken.
                if model_name.startswith(("gpt-4o", "o1", "o3", "gpt-5", "gpt-6")):
                    self.tokenizer = tiktoken.get_encoding("o200k_base")
                else:
                    self.tokenizer = tiktoken.get_encoding("cl100k_base")
            logger.info(
                "Token counter backend selected: OpenAI/tiktoken "
                "(model=%s, encoding=%s)",
                model_name,
                self.tokenizer.name,
            )
        else:
            self.backend = "transformers"
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_name, use_fast=True, token=HF_TOKEN or None
            )
            if not getattr(self.tokenizer, "chat_template", None):
                raise ValueError(
                    f"Tokenizer '{model_name}' has no chat_template. "
                    "Use an instruct/chat model or apply a template manually."
                )
            logger.info(
                "Token counter backend selected: Hugging Face (tokenizer_model=%s)",
                model_name,
            )

    @staticmethod
    def _looks_like_openai(model_name: str) -> bool:
        return model_name.startswith(
            (
                "gpt-3.5",
                "gpt-4",
                "gpt-5",
                "gpt-6",
                "gpt-4o",
                "o1",
                "o3",
                "text-embedding",
            )
        )

    @staticmethod
    def _validate_messages(messages: list[dict]) -> None:
        if not isinstance(messages, list):
            raise TypeError("messages must be a list[dict]")
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise TypeError(f"messages[{i}] must be a dict")
            if "role" not in msg or "content" not in msg:
                raise ValueError(f"messages[{i}] must contain 'role' and 'content'")

    def count_text(self, text: str) -> int:
        """Count tokens in a raw string."""
        if not text:
            return 0
        if self.backend == "tiktoken":
            return len(self.tokenizer.encode(text, disallowed_special=()))
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        tokenize: bool = False,
        add_generation_prompt: bool = True,
    ) -> str | list[int]:
        """Format chat messages with the model's chat template.

        Only valid for Hugging Face backends. For OpenAI, send the
        ``messages`` list directly to the API — do not pre-format ChatML.
        """
        self._validate_messages(messages)
        if self.backend == "tiktoken":
            raise NotImplementedError(
                "OpenAI does not use a local chat template. Send `messages` "
                "to the API directly, or use `count_chat_tokens()` for an "
                "estimate."
            )
        return self.tokenizer.apply_chat_template(
            messages,
            tokenize=tokenize,
            add_generation_prompt=add_generation_prompt,
        )

    def count_chat_tokens(
        self,
        messages: list[dict[str, str]],
        add_generation_prompt: bool = True,
        # OpenAI overhead (gpt-4 / gpt-4-turbo / gpt-4o family).
        tokens_per_message: int = 3,
        tokens_per_name: int = 1,
        reply_prime: int = 3,
    ) -> int:
        """Count tokens for a chat payload.

        - OpenAI: apply the documented per-message overhead formula.
        - Hugging Face: delegate to ``apply_chat_template(tokenize=True)``.
        """
        self._validate_messages(messages)

        if self.backend == "transformers":
            return len(
                self.tokenizer.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=add_generation_prompt,
                )
            )

        total = reply_prime
        for msg in messages:
            total += tokens_per_message
            for key, value in msg.items():
                total += len(self.tokenizer.encode(str(value), disallowed_special=()))
                if key == "name":
                    total += tokens_per_name
        return total


def _get_tokenizer(model_name: str) -> TokenCounter:
    """Memoize tokenizers so we never rebuild them per call."""
    tokenizer = _TOKENIZERS.get(model_name)
    if tokenizer is None:
        tokenizer = TokenCounter(model_name)
        _TOKENIZERS[model_name] = tokenizer
    return tokenizer


def _is_litellm_gateway() -> bool:
    """Return whether the configured base URL uses the Nexo LiteLLM route."""
    path = urlsplit(LLM_BASE_URL).path.rstrip("/")
    if path.endswith(("/gateway/v1", "/gateway")):
        return True
    # LiteLLM's default OpenAI-compatible route is /v1. Azure uses
    # /openai/v1, which is not a LiteLLM metadata endpoint.
    return path.endswith("/v1") and not path.endswith("/openai/v1")


def _model_info_url() -> str:
    """Build the LiteLLM model-info URL from the configured API base URL."""
    parts = urlsplit(LLM_BASE_URL.rstrip("/"))
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = f"{path}/model/info"
    else:
        path = f"{path}/v1/model/info"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def _resolve_tokenizer_model() -> str:
    """Resolve the Hugging Face tokenizer name exposed by LiteLLM."""
    global _RESOLVED_TOKENIZER_MODEL

    if _RESOLVED_TOKENIZER_MODEL:
        return _RESOLVED_TOKENIZER_MODEL
    if not _is_litellm_gateway():
        return LLM_MODEL_NAME

    async with _MODEL_RESOLUTION_LOCK:
        if _RESOLVED_TOKENIZER_MODEL:
            return _RESOLVED_TOKENIZER_MODEL

        url = f"{_model_info_url()}?model={quote(LLM_MODEL_NAME, safe='')}"
        headers = {}
        if LLM_API_KEY and LLM_API_KEY != "sk-no-key":
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
            if not isinstance(payload, dict):
                raise TypeError("LiteLLM model info response must be an object")

            model_entries = payload.get("data")
            if not isinstance(model_entries, list):
                raise TypeError("LiteLLM model info response missing 'data' list")

            model_entry = next(
                (
                    entry
                    for entry in model_entries
                    if isinstance(entry, dict)
                    and entry.get("model_name") == LLM_MODEL_NAME
                ),
                None,
            )
            if model_entry is None:
                raise RuntimeError(
                    "LiteLLM model info response has no exact entry for "
                    f"model '{LLM_MODEL_NAME}'"
                )

            model_info = model_entry.get("litellm_params")
            if not isinstance(model_info, dict):
                raise TypeError(
                    "LiteLLM model info entry missing 'litellm_params' object"
                )

            tokenizer_model = model_info.get("tokenizer_with_hf_repo")
            if not isinstance(tokenizer_model, str) or not tokenizer_model.strip():
                raise ValueError(
                    "LiteLLM model info did not include tokenizer_with_hf_repo"
                )
            _RESOLVED_TOKENIZER_MODEL = tokenizer_model.strip()
            logger.info(
                "Resolved tokenizer for model '%s' to '%s' via LiteLLM model info",
                LLM_MODEL_NAME,
                _RESOLVED_TOKENIZER_MODEL,
            )
        except (
            aiohttp.ClientError,
            KeyError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ) as error:
            logger.warning(
                "Could not resolve tokenizer for LiteLLM model '%s': %s. "
                "Falling back to configured model name.",
                LLM_MODEL_NAME,
                error,
            )
            _RESOLVED_TOKENIZER_MODEL = LLM_MODEL_NAME

    return _RESOLVED_TOKENIZER_MODEL


async def count_token(text_content: str) -> int:
    """Count tokens locally with an LRU cache + single-flight guard.

    Kept ``async`` for backward compatibility with callers that ``await``
    it. All work inside is synchronous (CPU-bound); there is no network
    call and therefore no ``await`` in the hot path.
    """
    if not text_content:
        return 0

    text_hash = hashlib.md5(
        text_content.encode("utf-8"), usedforsecurity=False
    ).hexdigest()

    if text_hash in _TOKEN_CACHE:
        _TOKEN_CACHE.move_to_end(text_hash)
        return _TOKEN_CACHE[text_hash]

    if text_hash in _INFLIGHT_TOKEN_REQUESTS:
        return await _INFLIGHT_TOKEN_REQUESTS[text_hash]

    loop = asyncio.get_running_loop()
    future: asyncio.Future[int] = loop.create_future()
    _INFLIGHT_TOKEN_REQUESTS[text_hash] = future

    tokenizer_model = None
    try:
        tokenizer_model = await _resolve_tokenizer_model()
        if tokenizer_model in _TOKENIZER_LOAD_ERRORS:
            token_count = max(1, len(text_content) // 4)
        else:
            token_count = _get_tokenizer(tokenizer_model).count_text(text_content)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        if tokenizer_model:
            _TOKENIZER_LOAD_ERRORS.add(tokenizer_model)
        # Conservative fallback: ~4 chars ≈ 1 token.
        token_count = max(1, len(text_content) // 4)
        logger.warning(
            "Token counter fallback selected: approximately 1 token per 4 "
            "characters (model=%s, text_length=%d, error=%s)",
            LLM_MODEL_NAME,
            len(text_content),
            error,
        )
    finally:
        if not future.done():
            future.set_result(token_count)
        _INFLIGHT_TOKEN_REQUESTS.pop(text_hash, None)

    if len(_TOKEN_CACHE) >= _MAX_CACHE_SIZE:
        _TOKEN_CACHE.popitem(last=False)
    _TOKEN_CACHE[text_hash] = token_count
    return token_count
