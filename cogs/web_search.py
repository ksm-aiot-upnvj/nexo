import logging

from discord.ext import commands

from schemas.web_search import WebSearchSchema
from services.web_search.ddgs_provider import search_web
from utils.schema_utils import get_clean_schema

logger = logging.getLogger("web_search")
TOOL_NAME = "web_search"
TOOL_DESCRIPTION = (
    "Search the public web for current information or sources. Returns up to five "
    "titles, URLs, and short snippets; it does not open full pages."
)


class WebSearch(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if not hasattr(self.bot, "ai_tools"):
            self.bot.ai_tools = []
        if not hasattr(self.bot, "local_tool_handlers"):
            self.bot.local_tool_handlers = {}

        self.bot.ai_tools = [
            tool
            for tool in self.bot.ai_tools
            if tool.get("function", {}).get("name") != TOOL_NAME
        ]
        self.bot.ai_tools.append(
            {
                "type": "function",
                "readOnlyHint": True,
                "function": {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "parameters": get_clean_schema(WebSearchSchema),
                },
            }
        )
        self.bot.local_tool_handlers[TOOL_NAME] = self.search_handler
        logger.info("Registered local tool (name=%s)", TOOL_NAME)

    async def cog_unload(self):
        self.bot.ai_tools = [
            tool
            for tool in getattr(self.bot, "ai_tools", [])
            if tool.get("function", {}).get("name") != TOOL_NAME
        ]
        getattr(self.bot, "local_tool_handlers", {}).pop(TOOL_NAME, None)

    async def search_handler(self, arguments: dict) -> str:
        validated = WebSearchSchema.model_validate(arguments)
        return await search_web(validated.query)


async def setup(bot):
    await bot.add_cog(WebSearch(bot))
