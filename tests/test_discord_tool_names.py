import pytest
from pydantic import ValidationError

from cogs.agent_orchestrator import AgentOrchestrator
from cogs.competition_radar import CompetitionRadar
from cogs.event_tools import TOOL_NAMES
from schemas.discord.moderation_tools import ClearMessagesSchema


def test_clear_messages_schema_caps_requests_at_100():
    assert ClearMessagesSchema().limit == 100
    assert ClearMessagesSchema(limit=100).limit == 100
    with pytest.raises(ValidationError):
        ClearMessagesSchema(limit=101)


def test_discord_tool_names_are_clear_and_english():
    assert "delete_channel_messages" in TOOL_NAMES
    assert "search_server_members" in TOOL_NAMES
    assert "mention_member_in_channel" in TOOL_NAMES
    assert "clear_messages" not in TOOL_NAMES
    assert "find_server_members" not in TOOL_NAMES


def test_competition_slash_options_use_english_names():
    competition_group = CompetitionRadar.competition_group
    assert competition_group.name == "competition"
    assert {
        parameter.name for parameter in competition_group.commands[0].parameters
    } == {
        "name",
        "deadline",
        "category",
        "registration_url",
        "guide_url",
        "details",
        "channel",
        "role",
    }


def test_token_usage_commands_distinguish_personal_and_server_scopes():
    assert AgentOrchestrator.token_stats_slash.name == "my-token-usage"
    assert AgentOrchestrator.leaderboard_token_slash.name == "server-token-leaderboard"
