import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent import orchestrator as agent_orchestrator
from services.agent.orchestrator import (
    _append_tool_results,
    _execute_tool_calls,
    _log_prompt_token_comparison,
)
from services.agent.tool_schema import (
    _validate_schema_value,
    _validate_tool_arguments,
    sanitize_tools_list,
)
from services.llm import client as llm_client
from services.mcp import client as mcp_client
from utils.schemas import DiscordEventSchema


def test_discord_event_schema_time_cleaning():
    payload = {
        "name": "Meeting Evaluasi",
        "description": "Evaluasi progres proyek bulanan.",
        "start_date": "2026-09-02",
        "start_time": "13:00",
        "location": "Voice Channel: Umum",
        "target_role": "@everyone",
    }
    event = DiscordEventSchema.model_validate(payload)
    assert event.name == "Meeting Evaluasi"
    assert event.start_time == "13:00:00"
    assert event.target_role == "@everyone"


def test_sanitize_tools_list():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A dummy tool description",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "param1": {"type": "string", "title": "Param 1"},
                    },
                    "required": ["param1"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    sanitized = sanitize_tools_list(tools)
    assert len(sanitized) == 1
    func = sanitized[0]["function"]
    assert func["name"] == "test_tool"
    # Title and additionalProperties should be cleaned
    props = func["parameters"]["properties"]["param1"]
    assert "title" not in props


@pytest.mark.asyncio
async def test_read_only_tool_calls_run_concurrently_and_keep_result_order():
    both_started = asyncio.Event()
    started = set()

    async def lookup(arguments):
        started.add(arguments["name"])
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=1)
        return arguments["name"]

    bot = type("Bot", (), {"local_tool_handlers": {"lookup": lookup}})()
    calls = [
        {"tool_name": "lookup", "arguments": {"name": "first"}, "error": None},
        {"tool_name": "lookup", "arguments": {"name": "second"}, "error": None},
    ]

    results = await _execute_tool_calls(bot, calls, {"lookup"}, None)

    assert results == ["first", "second"]


@pytest.mark.asyncio
async def test_mutating_tool_calls_remain_sequential():
    execution_order = []

    async def mutate(arguments):
        execution_order.append(f"start:{arguments['name']}")
        await asyncio.sleep(0)
        execution_order.append(f"done:{arguments['name']}")
        return arguments["name"]

    bot = type("Bot", (), {"local_tool_handlers": {"mutate": mutate}})()
    calls = [
        {"tool_name": "mutate", "arguments": {"name": "first"}, "error": None},
        {"tool_name": "mutate", "arguments": {"name": "second"}, "error": None},
    ]

    results = await _execute_tool_calls(bot, calls, set(), None)

    assert results == ["first", "second"]
    assert execution_order == [
        "start:first",
        "done:first",
        "start:second",
        "done:second",
    ]


def test_tool_results_keep_original_call_ids_and_order():
    messages = []
    calls = [
        {"tool_call": SimpleNamespace(id="call-1"), "tool_name": "first"},
        {"tool_call": SimpleNamespace(id="call-2"), "tool_name": "second"},
    ]

    _append_tool_results(messages, calls, ["result-1", "result-2"])

    assert [message["tool_call_id"] for message in messages] == [
        "call-1",
        "call-2",
    ]
    assert [message["content"] for message in messages] == ["result-1", "result-2"]


def test_tool_argument_schema_validation_checks_required_types_and_extra_fields():
    schema = {
        "type": "object",
        "properties": {"member_id": {"type": "integer"}},
        "required": ["member_id"],
        "additionalProperties": False,
    }

    assert _validate_schema_value({"member_id": 123}, schema) is None
    assert "must be integer" in _validate_schema_value({"member_id": "123"}, schema)
    assert "missing required" in _validate_schema_value({}, schema)
    assert "unexpected field" in _validate_schema_value(
        {"member_id": 123, "extra": True}, schema
    )
    assert "Unknown tool name" in _validate_tool_arguments(
        "unregistered", {"member_id": 123}, {"known": schema}
    )


def test_prompt_token_estimate_delta_is_logged(caplog):
    with caplog.at_level("INFO", logger="agent_orchestrator"):
        delta = _log_prompt_token_comparison(
            iteration=2, local_estimate=500, provider_usage=537
        )

    assert delta == 37
    assert "local_estimate=500" in caplog.text
    assert "provider_usage=537" in caplog.text


@pytest.mark.asyncio
async def test_request_budget_trims_oldest_history_and_rejects_unfit_prompt(
    monkeypatch,
):
    async def count_characters(text):
        return len(text)

    monkeypatch.setattr(agent_orchestrator, "count_token", count_characters)
    monkeypatch.setattr(
        agent_orchestrator, "get_prompt_estimate_safety_margin", lambda: 0
    )
    system_message = {"role": "system", "content": "base"}
    old_message = {"role": "user", "content": "old " * 30}
    current_user_message = {"role": "user", "content": "current"}
    messages = [system_message, old_message, current_user_message]

    estimate = await agent_orchestrator._trim_history_for_request(
        messages, current_user_message, [], context_window=100, completion_reserve=0
    )

    assert messages == [system_message, current_user_message]
    assert estimate <= 100

    oversized_prompt = {"role": "user", "content": "question " * 30}
    messages = [system_message, oversized_prompt]
    with pytest.raises(ValueError, match="exceed the model input budget"):
        await agent_orchestrator._trim_history_for_request(
            messages, oversized_prompt, [], context_window=100, completion_reserve=0
        )


@pytest.mark.asyncio
async def test_large_tool_result_uses_remaining_context_and_keeps_json_valid(
    monkeypatch,
):
    async def count_characters(text):
        return len(text)

    monkeypatch.setattr(agent_orchestrator, "count_token", count_characters)
    monkeypatch.setattr(
        agent_orchestrator, "get_prompt_estimate_safety_margin", lambda: 0
    )
    system_message = {"role": "system", "content": "system"}
    old_history = {"role": "user", "content": "old history " * 35}
    current_user_message = {"role": "user", "content": "current question"}
    assistant_tool_call = {"role": "assistant", "content": "calling tool"}
    tool_result = {
        "role": "tool",
        "tool_call_id": "call-1",
        "name": "lookup",
        "content": json.dumps({"items": ["entry " * 60]}, ensure_ascii=False),
    }
    messages = [
        system_message,
        old_history,
        current_user_message,
        assistant_tool_call,
        tool_result,
    ]

    estimate = await agent_orchestrator._trim_history_for_request(
        messages,
        current_user_message,
        [],
        context_window=480,
        completion_reserve=0,
    )

    assert old_history not in messages
    assert tool_result in messages
    assert estimate <= 480
    parsed_result = json.loads(tool_result["content"])
    assert parsed_result["truncated"] is True
    assert "preview" in parsed_result


@pytest.mark.asyncio
async def test_mcp_tool_timeout_returns_a_failure(monkeypatch):
    class HangingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def call_tool(self, _name, _arguments):
            await asyncio.Event().wait()

    @asynccontextmanager
    async def fake_sse(_url):
        yield object(), object()

    monkeypatch.setattr(mcp_client, "MCP_TOOL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(mcp_client, "sse_client", fake_sse)
    monkeypatch.setattr(mcp_client, "ClientSession", lambda *_args: HangingSession())

    with pytest.raises(RuntimeError, match="exceeded the 0.01-second timeout"):
        await mcp_client.execute_mcp_tool("slow_tool", {})


@pytest.mark.asyncio
async def test_llm_health_check_caches_success_until_ttl_expires(monkeypatch):
    now = [100.0]
    client = MagicMock()
    client.models.list = AsyncMock()
    with_options = MagicMock(return_value=client)
    monkeypatch.setattr(llm_client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(llm_client.ai_client, "with_options", with_options)
    monkeypatch.setattr(llm_client, "_LLM_HEALTH_CACHE", None)

    assert await llm_client.check_llm_health() == (True, "LLM provider is online")
    assert await llm_client.check_llm_health() == (True, "LLM provider is online")
    assert client.models.list.await_count == 1

    now[0] += llm_client.LLM_HEALTH_CACHE_TTL_SECONDS + 1
    assert await llm_client.check_llm_health() == (True, "LLM provider is online")
    assert client.models.list.await_count == 2
