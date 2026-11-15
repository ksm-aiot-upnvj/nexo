import logging

import aiohttp

from services.llm.client import check_llm_health
from services.mcp.client import MCP_SERVER_URL

logger = logging.getLogger("service_health")


async def check_services_health():
    """Returns True if both services are up, False and an error message otherwise."""
    try:
        llm_ok, llm_message = await check_llm_health()
        if not llm_ok:
            return False, llm_message

        async with aiohttp.ClientSession() as session:
            # Check MCP server
            try:
                mcp_ok = False
                # 1. Try /sse endpoint
                try:
                    async with session.get(
                        f"{MCP_SERVER_URL.rstrip('/')}/sse",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as response:
                        if response.status in (200, 400, 405):
                            mcp_ok = True
                except Exception as e:  # noqa: BLE001
                    logger.debug("MCP SSE health check failed: %s", e)

                # 2. Try root endpoint if /sse was not reached
                if not mcp_ok:
                    async with session.get(
                        f"{MCP_SERVER_URL}/", timeout=aiohttp.ClientTimeout(total=2)
                    ) as response:
                        if response.status in (200, 400, 405):
                            mcp_ok = True

                if not mcp_ok:
                    logger.warning("MCP health check returned no healthy endpoint")
                    return (
                        False,
                        f"**MCP Server** responded with an error ({response.status}).",
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP server health check failed: %s", e)
                return (
                    False,
                    "**MCP Server** is OFFLINE. Please start the MCP server.",
                )

    except Exception as e:
        logger.exception("Unexpected error while checking service health")
        return False, f"An error occurred while checking servers: {e!s}"

    return True, "All systems go"
