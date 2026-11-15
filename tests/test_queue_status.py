import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.agent_orchestrator import (
    AgentOrchestrator,
    QueuedLLMRequest,
)


@pytest.mark.asyncio
async def test_starting_queued_request_updates_remaining_positions(monkeypatch):
    bot = SimpleNamespace(
        message_queue=asyncio.Queue(),
        bot_lock=asyncio.Lock(),
    )
    cog = AgentOrchestrator(bot)
    sleep = AsyncMock()
    monkeypatch.setattr("cogs.agent_orchestrator.asyncio.sleep", sleep)

    first_status = SimpleNamespace(edit=AsyncMock())
    second_status = SimpleNamespace(edit=AsyncMock())
    third_status = SimpleNamespace(edit=AsyncMock())
    first = QueuedLLMRequest(
        context=object(),
        prompt="first",
        queued_at=0,
        status_message=first_status,
        last_queue_position=2,
    )
    second = QueuedLLMRequest(
        context=object(),
        prompt="second",
        queued_at=0,
        status_message=second_status,
        last_queue_position=3,
    )
    third = QueuedLLMRequest(
        context=object(),
        prompt="third",
        queued_at=0,
        status_message=third_status,
        last_queue_position=4,
    )
    cog._pending_requests = [first, second, third]

    cog._mark_request_started(first)
    await cog._queue_position_refresh_task

    assert cog._pending_requests == [second, third]
    first_status.edit.assert_not_awaited()
    second_status.edit.assert_awaited_once_with(
        content="⏳ *Your question is currently queued at position #1. Please wait a moment!*"
    )
    third_status.edit.assert_awaited_once_with(
        content="⏳ *Your question is currently queued at position #2. Please wait a moment!*"
    )
    assert second.last_queue_position == 1
    assert third.last_queue_position == 2
    sleep.assert_awaited_once_with(1.1)
