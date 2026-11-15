import json
from types import SimpleNamespace

import pytest

from cogs.web_search import TOOL_NAME, WebSearch
from services.web_search import ddgs_provider


def test_sync_search_limits_results_and_field_lengths(monkeypatch):
    class FakeDDGS:
        def __init__(self, *, timeout):
            assert timeout == ddgs_provider.DDGS_TIMEOUT_SECONDS

        def text(self, query, **options):
            assert query == "hydroponic EC"
            assert options["max_results"] == ddgs_provider.MAX_RESULTS
            assert options["region"] == "id-id"
            return [
                {
                    "title": "t" * (ddgs_provider.MAX_TITLE_CHARS + 10),
                    "href": "https://example.com",
                    "body": "s" * (ddgs_provider.MAX_SNIPPET_CHARS + 10),
                }
                for _ in range(ddgs_provider.MAX_RESULTS + 2)
            ]

    monkeypatch.setattr(ddgs_provider, "DDGS", FakeDDGS)

    results = ddgs_provider._search_sync("hydroponic EC")

    assert len(results) == ddgs_provider.MAX_RESULTS
    assert len(results[0]["title"]) == ddgs_provider.MAX_TITLE_CHARS
    assert len(results[0]["snippet"]) == ddgs_provider.MAX_SNIPPET_CHARS


@pytest.mark.asyncio
async def test_search_web_returns_bounded_results_as_json(monkeypatch):
    monkeypatch.setattr(
        ddgs_provider,
        "_search_sync",
        lambda _query: [
            {"title": "Result", "url": "https://example.com", "snippet": "Text"}
        ],
    )

    response = json.loads(await ddgs_provider.search_web(" current info "))

    assert response["results"][0]["url"] == "https://example.com"
    assert "untrusted external content" in response["source"]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [" ", "x", "q" * 301])
async def test_search_web_rejects_invalid_query(query):
    with pytest.raises(ValueError, match="between 2 and 300"):
        await ddgs_provider.search_web(query)


@pytest.mark.asyncio
async def test_search_web_returns_safe_message_on_provider_failure(monkeypatch):
    def fail(_query):
        raise RuntimeError("provider error with sensitive detail")

    monkeypatch.setattr(ddgs_provider, "_search_sync", fail)

    response = await ddgs_provider.search_web("current information")

    assert response == "Web search is temporarily unavailable. Please try again later."


@pytest.mark.asyncio
async def test_web_search_cog_registers_and_removes_local_tool():
    bot = SimpleNamespace(ai_tools=[], local_tool_handlers={})
    cog = WebSearch(bot)

    await cog.cog_load()

    assert bot.ai_tools[0]["function"]["name"] == TOOL_NAME
    assert bot.ai_tools[0]["readOnlyHint"] is True
    assert TOOL_NAME in bot.local_tool_handlers

    await cog.cog_unload()

    assert bot.ai_tools == []
    assert TOOL_NAME not in bot.local_tool_handlers
