import asyncio
import hashlib
import logging
import math
import threading
from collections import OrderedDict, deque
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
HF_HUB_OFFLINE = settings.HF_HUB_OFFLINE
ai_client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY or "sk-no-key",
    timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
    max_retries=settings.LLM_MAX_RETRIES,
)

MAX_COMPLETION_TOKENS = 1024
TOOL_BUFFER_TOKENS = 1200
PROMPT_ESTIMATE_MIN_SAMPLES = 10
PROMPT_ESTIMATE_HISTORY_SIZE = 50
PROMPT_ESTIMATE_MAX_MARGIN = 512
_PROMPT_ESTIMATE_SAFETY_MARGIN = 128
_PROMPT_ESTIMATE_GAPS: deque[int] = deque(maxlen=PROMPT_ESTIMATE_HISTORY_SIZE)

_TOKEN_CACHE: OrderedDict[str, int] = OrderedDict()
_MAX_CACHE_SIZE = 1000
_INFLIGHT_TOKEN_REQUESTS: dict[str, asyncio.Future[int]] = {}
_TOKENIZERS: dict[str, TokenCounter] = {}
_TOKENIZER_LOAD_ERRORS: set[str] = set()
_RESOLVED_TOKENIZER_MODEL: str | None = None
_RESOLVED_CONTEXT_WINDOW: int | None = None
_MODEL_METADATA_RESOLVED = False
_MODEL_RESOLUTION_LOCK = asyncio.Lock()
_TOKENIZER_LOAD_LOCK = threading.Lock()


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
            tokenizer_options = {"use_fast": True, "token": HF_TOKEN or None}
            try:
                self.tokenizer = AutoTokenizer.from_pretrained(
                    model_name,
                    **tokenizer_options,
                    local_files_only=True,
                )
            except OSError as cache_error:
                if HF_HUB_OFFLINE:
                    raise
                logger.info(
                    "Tokenizer cache miss; trying Hugging Face download (model=%s, error=%s)",
                    model_name,
                    cache_error,
                )
                self.tokenizer = AutoTokenizer.from_pretrained(
                    model_name,
                    **tokenizer_options,
                    local_files_only=False,
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
    with _TOKENIZER_LOAD_LOCK:
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


def parse_litellm_model_metadata(
    payload: dict, model_name: str
) -> tuple[str | None, int | None]:
    """Extract tokenizer repository and input limit for one configured model."""
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise TypeError("LiteLLM model info response is missing its data list")

    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and item.get("model_name") == model_name
        ),
        None,
    )
    if entry is None:
        raise ValueError(f"LiteLLM returned no metadata for '{model_name}'")

    params = entry.get("litellm_params")
    if not isinstance(params, dict):
        params = {}
    model_info = entry.get("model_info")
    if not isinstance(model_info, dict):
        model_info = {}

    tokenizer_model = params.get("tokenizer_with_hf_repo")
    if not isinstance(tokenizer_model, str) or not tokenizer_model.strip():
        tokenizer_model = None

    context_window = model_info.get("max_input_tokens")
    if (
        not isinstance(context_window, int)
        or isinstance(context_window, bool)
        or context_window <= 0
    ):
        context_window = None

    return tokenizer_model.strip() if tokenizer_model else None, context_window


async def _resolve_tokenizer_model() -> str:
    """Resolve the tokenizer name using cached LiteLLM model metadata."""
    await _resolve_model_metadata()
    return _RESOLVED_TOKENIZER_MODEL or LLM_MODEL_NAME


def _effective_context_window() -> int:
    """Apply the configured hard cap to the provider-reported context limit."""
    return min(_RESOLVED_CONTEXT_WINDOW or TOTAL_CONTEXT_WINDOW, TOTAL_CONTEXT_WINDOW)


async def _resolve_model_metadata() -> None:
    """Load tokenizer and input-context metadata once per process."""
    global _MODEL_METADATA_RESOLVED, _RESOLVED_CONTEXT_WINDOW
    global _RESOLVED_TOKENIZER_MODEL

    if _MODEL_METADATA_RESOLVED:
        return
    if not _is_litellm_gateway():
        _RESOLVED_TOKENIZER_MODEL = LLM_MODEL_NAME
        _MODEL_METADATA_RESOLVED = True
        return

    async with _MODEL_RESOLUTION_LOCK:
        if _MODEL_METADATA_RESOLVED:
            return

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

            tokenizer_model, context_window = parse_litellm_model_metadata(
                payload, LLM_MODEL_NAME
            )
            if tokenizer_model:
                _RESOLVED_TOKENIZER_MODEL = tokenizer_model
            if context_window:
                _RESOLVED_CONTEXT_WINDOW = context_window

            logger.info(
                "Resolved LiteLLM metadata (model=%s, provider_context_window=%s, effective_context_window=%s, tokenizer=%s)",
                LLM_MODEL_NAME,
                _RESOLVED_CONTEXT_WINDOW,
                _effective_context_window(),
                _RESOLVED_TOKENIZER_MODEL or LLM_MODEL_NAME,
            )
        except (aiohttp.ClientError, TimeoutError, TypeError, ValueError) as error:
            logger.warning(
                "Could not resolve LiteLLM metadata for model %s: %s",
                LLM_MODEL_NAME,
                error,
            )

        if not _RESOLVED_TOKENIZER_MODEL:
            _RESOLVED_TOKENIZER_MODEL = LLM_MODEL_NAME
        if _RESOLVED_CONTEXT_WINDOW is None:
            logger.warning(
                "Using configured TOTAL_CONTEXT_WINDOW fallback (%s)",
                TOTAL_CONTEXT_WINDOW,
            )
        _MODEL_METADATA_RESOLVED = True


async def get_context_budget(
    prompt_overhead_tokens: int,
    tool_buffer_tokens: int = TOOL_BUFFER_TOKENS,
) -> tuple[int, int, int]:
    """Return the active input limit, history budget, and retained-history budget."""
    await _resolve_model_metadata()
    context_window = _effective_context_window()
    history_budget = max(
        0,
        context_window
        - MAX_COMPLETION_TOKENS
        - max(0, prompt_overhead_tokens)
        - get_prompt_estimate_safety_margin()
        - max(0, tool_buffer_tokens),
    )
    return context_window, history_budget, int(history_budget * 0.40)


def get_prompt_estimate_safety_margin() -> int:
    """Return the current bounded margin for request-serialization overhead."""
    return _PROMPT_ESTIMATE_SAFETY_MARGIN


def record_prompt_token_usage(local_estimate: int, provider_usage: int) -> int:
    """Learn a conservative prompt margin after enough provider observations."""
    global _PROMPT_ESTIMATE_SAFETY_MARGIN

    gap = max(0, provider_usage - local_estimate)
    _PROMPT_ESTIMATE_GAPS.append(gap)
    if len(_PROMPT_ESTIMATE_GAPS) >= PROMPT_ESTIMATE_MIN_SAMPLES:
        sorted_gaps = sorted(_PROMPT_ESTIMATE_GAPS)
        p95_index = max(0, math.ceil(0.95 * len(sorted_gaps)) - 1)
        observed_p95 = sorted_gaps[p95_index]
        _PROMPT_ESTIMATE_SAFETY_MARGIN = min(
            PROMPT_ESTIMATE_MAX_MARGIN,
            max(128, observed_p95 + 64),
        )
    return gap


async def count_chat_messages(messages: list[dict[str, str]]) -> int:
    """Count serialized chat messages with the configured model tokenizer/template."""
    tokenizer_model = await _resolve_tokenizer_model()
    tokenizer = await asyncio.to_thread(_get_tokenizer, tokenizer_model)
    return await asyncio.to_thread(tokenizer.count_chat_tokens, messages)


async def count_token(text_content: str) -> int:
    """Count tokens with an LRU cache and shared in-flight requests.

    The first cache miss may resolve LiteLLM metadata and load the tokenizer.
    Tokenizer loading and counting run in worker threads to keep the event loop responsive.
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
    token_count = max(1, len(text_content) // 4)
    try:
        tokenizer_model = await _resolve_tokenizer_model()
        if tokenizer_model in _TOKENIZER_LOAD_ERRORS:
            token_count = max(1, len(text_content) // 4)
        else:
            tokenizer = await asyncio.to_thread(_get_tokenizer, tokenizer_model)
            token_count = await asyncio.to_thread(tokenizer.count_text, text_content)
    except Exception as error:  # noqa: BLE001
        # Tokenization must not make the chat path unavailable.
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
