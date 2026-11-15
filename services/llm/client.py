import asyncio
import logging
import time

from openai import APIError, AsyncOpenAI

from config.settings import settings

logger = logging.getLogger("llm_client")
LLM_HEALTH_CACHE_TTL_SECONDS = 10.0
LLM_HEALTH_FAILURE_CACHE_TTL_SECONDS = 2.0
_LLM_HEALTH_CACHE: tuple[float, bool, str] | None = None
_LLM_HEALTH_CHECK_LOCK = asyncio.Lock()

LLM_BASE_URL = settings.LLM_BASE_URL
LLM_API_KEY = settings.LLM_API_KEY.strip("\"' \r\n")
LLM_MODEL_NAME = settings.LLM_MODEL_NAME

try:
    with open("prompts/system_prompt.md", "r", encoding="utf-8") as prompt_file:
        SYSTEM_PROMPT = prompt_file.read().strip()
except FileNotFoundError:
    SYSTEM_PROMPT = "You are Nexo, a helpful assistant."

ai_client = AsyncOpenAI(
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY or "sk-no-key",
    timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS,
    max_retries=settings.LLM_MAX_RETRIES,
)


async def warm_up_llm() -> bool:
    """Run a tiny inference request to load the configured model into its runtime."""
    started_at = time.perf_counter()
    timeout = min(settings.LLM_REQUEST_TIMEOUT_SECONDS, 150.0)

    try:
        client = ai_client.with_options(timeout=timeout, max_retries=0)
        await client.chat.completions.create(
            model=LLM_MODEL_NAME,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_completion_tokens=1,
            temperature=0,
        )
    except Exception as error:  # noqa: BLE001
        logger.warning(
            "LLM inference warm-up failed (model=%s, error_type=%s)",
            LLM_MODEL_NAME,
            type(error).__name__,
        )
        return False

    logger.info(
        "LLM inference warm-up completed (model=%s, elapsed_seconds=%.2f)",
        LLM_MODEL_NAME,
        time.perf_counter() - started_at,
    )
    return True


async def check_llm_health():
    """Check the configured provider through its OpenAI-compatible models API."""
    global _LLM_HEALTH_CACHE

    async with _LLM_HEALTH_CHECK_LOCK:
        now = time.monotonic()
        if _LLM_HEALTH_CACHE and now < _LLM_HEALTH_CACHE[0]:
            return _LLM_HEALTH_CACHE[1], _LLM_HEALTH_CACHE[2]

        try:
            client = ai_client.with_options(timeout=2.0)
            await client.models.list()
            result = (True, "LLM provider is online")
            cache_ttl = LLM_HEALTH_CACHE_TTL_SECONDS
        except (APIError, TimeoutError) as error:
            logger.warning("LLM provider health check failed: %s", error)
            result = (False, f"**LLM provider** is unavailable. Error: {error}")
            cache_ttl = LLM_HEALTH_FAILURE_CACHE_TTL_SECONDS

        _LLM_HEALTH_CACHE = (time.monotonic() + cache_ttl, *result)
        return result
