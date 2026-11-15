from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from services.llm import client as llm_client


@pytest.mark.asyncio
async def test_warm_up_llm_sends_one_short_completion(monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock()
    openai_client = MagicMock()
    openai_client.with_options.return_value = client
    monkeypatch.setattr(llm_client, "ai_client", openai_client)

    assert await llm_client.warm_up_llm() is True

    openai_client.with_options.assert_called_once_with(
        timeout=min(settings.LLM_REQUEST_TIMEOUT_SECONDS, 150.0), max_retries=0
    )
    client.chat.completions.create.assert_awaited_once_with(
        model=llm_client.LLM_MODEL_NAME,
        messages=[{"role": "user", "content": "Reply with OK."}],
        max_completion_tokens=1,
        temperature=0,
    )


@pytest.mark.asyncio
async def test_warm_up_llm_failure_does_not_log_exception_text(monkeypatch, caplog):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("sensitive provider response")
    )
    openai_client = MagicMock()
    openai_client.with_options.return_value = client
    monkeypatch.setattr(llm_client, "ai_client", openai_client)

    assert await llm_client.warm_up_llm() is False
    assert "RuntimeError" in caplog.text
    assert "sensitive provider response" not in caplog.text
