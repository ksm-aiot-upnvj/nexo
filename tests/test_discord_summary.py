import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.llm import discord_summary


def make_message(message_id: int, content: str, created_at: datetime):
    return SimpleNamespace(
        id=message_id,
        content=content,
        clean_content=content,
        author=SimpleNamespace(display_name=f"User {message_id}"),
        created_at=created_at,
        attachments=[],
    )


@pytest.mark.asyncio
async def test_build_summary_context_keeps_whole_recent_messages(monkeypatch):
    now = datetime.now(UTC)
    newest_first = [
        make_message(3, "newest bubble", now),
        make_message(2, "middle bubble", now - timedelta(minutes=1)),
        make_message(1, "oldest bubble", now - timedelta(minutes=2)),
    ]
    monkeypatch.setattr(
        discord_summary,
        "get_context_budget",
        AsyncMock(return_value=(1050, 30, 0)),
    )

    async def fake_count(messages):
        serialized = messages[-1]["content"]
        return 50 if "middle bubble" in serialized else 20

    monkeypatch.setattr(discord_summary, "count_chat_messages", fake_count)

    prompt, metadata = await discord_summary.build_summary_context(
        newest_first, requested_limit=None
    )

    assert metadata.scanned_count == 3
    assert metadata.included_count == 1
    assert metadata.excluded_from_scanned == 2
    assert "newest bubble" in prompt[-1]["content"]
    assert "middle bubble" not in prompt[-1]["content"]
    assert "up to 3 messages" in metadata.warning()
    assert "processed the 1 newest complete messages" in metadata.warning()
    assert metadata.requested_prompt_tokens == 50
    assert metadata.safe_input_budget == 26


@pytest.mark.asyncio
async def test_build_summary_context_orders_included_messages_chronologically(
    monkeypatch,
):
    now = datetime.now(UTC)
    newest_first = [
        make_message(3, "newest bubble", now),
        make_message(2, "middle bubble", now - timedelta(minutes=1)),
    ]
    monkeypatch.setattr(
        discord_summary,
        "get_context_budget",
        AsyncMock(return_value=(4096, 2048, 300)),
    )
    monkeypatch.setattr(
        discord_summary, "count_chat_messages", AsyncMock(return_value=100)
    )

    prompt, metadata = await discord_summary.build_summary_context(
        newest_first, requested_limit=2
    )

    assert metadata.included_count == 2
    assert prompt[-1]["content"].index("middle bubble") < prompt[-1]["content"].index(
        "newest bubble"
    )
    assert metadata.warning() is None


@pytest.mark.asyncio
async def test_summary_reports_requested_token_budget_and_whole_message_count(
    monkeypatch,
):
    now = datetime.now(UTC)
    newest_first = [
        make_message(
            message_id,
            f"message {message_id}",
            now - timedelta(minutes=message_id),
        )
        for message_id in range(1, 101)
    ]
    monkeypatch.setattr(
        discord_summary,
        "get_context_budget",
        AsyncMock(return_value=(2048, 1100, 0)),
    )

    async def fake_count(messages):
        scope = json.loads(messages[-1]["content"].split(":\n", 1)[1])
        return scope["message_count"] * 42

    monkeypatch.setattr(discord_summary, "count_chat_messages", fake_count)

    _, metadata = await discord_summary.build_summary_context(
        newest_first, requested_limit=100
    )

    assert metadata.requested_prompt_tokens == 4200
    assert metadata.safe_input_budget == 1024
    assert metadata.included_count == 24
    assert metadata.excluded_from_scanned == 76
    assert "up to 100 messages" in metadata.warning()
    assert "processed the 24 newest complete messages" in metadata.warning()
