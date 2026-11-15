import asyncio
import json
import logging

import aiohttp
from mcp import ClientSession
from mcp.client.sse import sse_client

from config.settings import settings

logger = logging.getLogger("mcp_client")
MCP_SERVER_URL = settings.MCP_SERVER_URL
MCP_TOOL_TIMEOUT_SECONDS = 30.0


async def get_tools_from_mcp_server() -> list:
    # Try a couple of likely endpoints so this client is resilient to
    # whether the streamable MCP app is mounted at `/` or `/mcp`.
    candidates = [MCP_SERVER_URL.rstrip("/") + "/sse"]

    def _get_field(obj, key):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    last_exc = None
    for url in candidates:
        try:
            async with sse_client(url) as (read_stream, write_stream):  # noqa: SIM117
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools_response = await session.list_tools()

                    llm_tools = []
                    for tool in getattr(tools_response, "tools", []) or []:
                        name = _get_field(tool, "name")
                        desc = _get_field(tool, "description") or ""

                        # input schema may be provided under different names or as a string
                        raw_schema = (
                            _get_field(tool, "inputSchema")
                            or _get_field(tool, "input_schema")
                            or _get_field(tool, "parameters")
                            or _get_field(tool, "schema")
                        )

                        parameters = None
                        if raw_schema:
                            if isinstance(raw_schema, str):
                                try:
                                    parameters = json.loads(raw_schema)
                                except Exception:  # noqa: BLE001
                                    parameters = None
                            elif isinstance(raw_schema, dict):
                                parameters = raw_schema

                        if not parameters:
                            parameters = {
                                "type": "object",
                                "properties": {},
                                "additionalProperties": True,
                            }

                        if name:
                            llm_tool = {
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "description": desc,
                                    "parameters": parameters,
                                },
                            }
                            annotations = _get_field(tool, "annotations")
                            if _get_field(annotations, "readOnlyHint") is True:
                                llm_tool["readOnlyHint"] = True
                            llm_tools.append(llm_tool)

                    return llm_tools
        except Exception as e:  # noqa: BLE001
            last_exc = e
            continue

    # If we couldn't connect to any candidate URL, raise a clear error.
    raise RuntimeError(f"Unable to fetch tools from MCP server: {last_exc}")


async def execute_mcp_tool(tool_name: str, arguments: dict) -> str:
    """
    Execute an MCP tool by calling the actual MCP server.
    """
    candidates = [MCP_SERVER_URL.rstrip("/") + "/sse"]
    for url in candidates:
        try:
            async with asyncio.timeout(MCP_TOOL_TIMEOUT_SECONDS):
                async with sse_client(url) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(tool_name, arguments)

                    if getattr(result, "isError", False):
                        raise RuntimeError(
                            f"MCP server reported failure for '{tool_name}'."
                        )

                    if hasattr(result, "content") and isinstance(result.content, list):
                        text_results = []
                        for content in result.content:
                            if content.type == "text":
                                text_results.append(content.text)

                        combined = "\n".join(text_results)
                        return combined
                    else:
                        return str(result)
        except TimeoutError:
            logger.error(
                "MCP tool call timed out (tool=%s, timeout_seconds=%s)",
                tool_name,
                MCP_TOOL_TIMEOUT_SECONDS,
            )
            raise RuntimeError(
                f"Tool '{tool_name}' exceeded the {MCP_TOOL_TIMEOUT_SECONDS}-second timeout."
            ) from None
        except aiohttp.ClientError, OSError, RuntimeError, ValueError:
            logger.exception(
                "MCP tool call failed (tool=%s, endpoint=%s)", tool_name, url
            )
            continue

    raise RuntimeError(
        f"Tool '{tool_name}' failed because the MCP server could not complete the request."
    )
