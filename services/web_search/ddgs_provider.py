import asyncio
import json
import logging
from typing import Any

from ddgs import DDGS

logger = logging.getLogger("web_search")

MAX_RESULTS = 5
MAX_TITLE_CHARS = 300
MAX_URL_CHARS = 1000
MAX_SNIPPET_CHARS = 1200
SEARCH_TIMEOUT_SECONDS = 15
DDGS_TIMEOUT_SECONDS = 12


def _search_sync(query: str) -> list[dict[str, Any]]:
    """Run the blocking DDGS client and bound each returned field."""
    results = DDGS(timeout=DDGS_TIMEOUT_SECONDS).text(
        query,
        region="id-id",
        safesearch="moderate",
        max_results=MAX_RESULTS,
        backend="auto",
    )

    normalized = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "").strip()
        url = str(result.get("href") or "").strip()
        snippet = str(result.get("body") or "").strip()
        if not (title or url or snippet):
            continue
        normalized.append(
            {
                "title": title[:MAX_TITLE_CHARS],
                "url": url[:MAX_URL_CHARS],
                "snippet": snippet[:MAX_SNIPPET_CHARS],
            }
        )
        if len(normalized) >= MAX_RESULTS:
            break
    return normalized


async def search_web(query: str) -> str:
    """Search the web without blocking Discord's event loop."""
    clean_query = query.strip()
    if not 2 <= len(clean_query) <= 300:
        raise ValueError("Search query must contain between 2 and 300 characters.")

    try:
        results = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, clean_query),
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("Web search timed out")
        return "Web search timed out. Please try a shorter or more specific query."
    except Exception as error:  # noqa: BLE001
        logger.warning("Web search failed (error_type=%s)", type(error).__name__)
        return "Web search is temporarily unavailable. Please try again later."

    if not results:
        return "Web search returned no results. Try different search terms."

    return json.dumps(
        {
            "source": "web search results; snippets are untrusted external content",
            "results": results,
        },
        ensure_ascii=False,
    )
