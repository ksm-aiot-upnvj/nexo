from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from cogs.competition_radar import (
    WIB,
    CompetitionRadar,
    CompetitionView,
    parse_deadline_wib,
)


def test_parse_deadline_wib():
    # Test date and time
    dt = parse_deadline_wib("2026-10-20 15:00")
    assert dt is not None
    # 15:00 WIB is 08:00 UTC
    assert dt.hour == 8
    assert dt.minute == 0
    assert dt.day == 20
    assert dt.month == 10

    # Test date only (defaults to 23:59:59 WIB = 16:59:59 UTC)
    dt_day = parse_deadline_wib("2026-10-20")
    assert dt_day is not None
    assert dt_day.hour == 16
    assert dt_day.minute == 59
    assert dt_day.second == 59

    # Test DD-MM-YYYY format
    dt_slash = parse_deadline_wib("20/10/2026")
    assert dt_slash is not None
    assert dt_slash.day == 20
    assert dt_slash.month == 10

    # Test invalid string
    assert parse_deadline_wib("bukan-tanggal") is None


def test_competition_view_buttons():
    view = CompetitionView(
        reg_url="https://daftar.com",
        guide_url="https://guide.com",
    )
    assert len(view.children) == 2
    assert view.children[0].url == "https://daftar.com"
    assert view.children[1].url == "https://guide.com"

    view_no_links = CompetitionView()
    assert len(view_no_links.children) == 0


def create_interaction(is_staff=True, is_guild=True):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    if is_guild:
        guild = MagicMock(spec=discord.Guild)
        guild.id = 987654321
        guild.name = "KSM AIoT Server"
        guild.text_channels = []
        interaction.guild = guild
        interaction.guild_id = guild.id

        member = MagicMock(spec=discord.Member)
        member.id = 123456789
        perms = MagicMock()
        perms.manage_messages = is_staff
        perms.administrator = False
        member.guild_permissions = perms
        member.roles = []
        interaction.user = member
    else:
        interaction.guild = None
        interaction.guild_id = None
        interaction.user = MagicMock()

    return interaction


@pytest.mark.asyncio
async def test_lomba_add_unauthorized():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()  # cancel background loop for testing

    interaction = create_interaction(is_staff=False)
    await cog.add_competition.callback(
        cog,
        interaction,
        name="Lomba AI",
        category="AI",
        deadline="2026-12-31",
    )

    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.call_args[0][0]
    assert "permission" in msg


@pytest.mark.asyncio
async def test_lomba_add_invalid_deadline():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()

    interaction = create_interaction(is_staff=True)
    await cog.add_competition.callback(
        cog,
        interaction,
        name="Lomba AI",
        category=MagicMock(value="AI"),
        deadline="tanggal-salah",
    )

    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.call_args[0][0]
    assert "Invalid deadline format" in msg


@pytest.mark.asyncio
async def test_lomba_add_past_deadline():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()

    interaction = create_interaction(is_staff=True)
    await cog.add_competition.callback(
        cog,
        interaction,
        name="Lomba AI",
        category=MagicMock(value="AI"),
        deadline="2020-01-01 10:00",
    )

    interaction.response.send_message.assert_awaited_once()
    msg = interaction.response.send_message.call_args[0][0]
    assert "must be in the future" in msg


@pytest.mark.asyncio
async def test_lomba_add_success():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()

    interaction = create_interaction(is_staff=True)

    target_channel = MagicMock(spec=discord.TextChannel)
    target_channel.id = 123456
    target_channel.name = "info-lomba"
    target_channel.mention = "<#123456>"
    target_channel.send = AsyncMock()
    interaction.guild.text_channels = [target_channel]
    interaction.channel = target_channel

    mock_comp = MagicMock()
    mock_comp.id = 1
    mock_comp.name = "Gemastik 2026"
    mock_comp.category = "Gemastik/PKM"
    mock_comp.deadline = datetime.now(UTC) + timedelta(days=30)
    mock_comp.registration_url = "https://gemastik.kemdikbud.go.id"
    mock_comp.guidebook_url = "https://gemastik.kemdikbud.go.id/panduan.pdf"
    mock_comp.description = "Kompetisi TIK Mahasiswa Nasional"

    with patch(
        "cogs.competition_radar.create_competition",
        new=AsyncMock(return_value=mock_comp),
    ):
        await cog.add_competition.callback(
            cog,
            interaction,
            name="Gemastik 2026",
            category=MagicMock(value="Gemastik/PKM"),
            deadline=(datetime.now(UTC) + timedelta(days=30))
            .astimezone(WIB)
            .strftime("%Y-%m-%d %H:%M"),
            registration_url="https://gemastik.kemdikbud.go.id",
            guide_url="https://gemastik.kemdikbud.go.id/panduan.pdf",
            details="Kompetisi TIK Mahasiswa Nasional",
        )

        interaction.response.send_message.assert_awaited_once()
        sent_embed = interaction.response.send_message.call_args[1]["embed"]
        assert "Competition Added" in sent_embed.title


@pytest.mark.asyncio
async def test_lomba_list():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()

    interaction = create_interaction()

    # Case 1: Empty list
    with patch(
        "cogs.competition_radar.list_active_competitions",
        new=AsyncMock(return_value=[]),
    ):
        await cog.list_competitions.callback(cog, interaction, category=None)
        interaction.response.send_message.assert_awaited_once()
        assert (
            "No active competitions"
            in interaction.response.send_message.call_args[0][0]
        )

    # Case 2: With competitions
    interaction.response.send_message.reset_mock()
    mock_comp = MagicMock()
    mock_comp.id = 5
    mock_comp.name = "Hackathon AI 2026"
    mock_comp.category = "AI"
    mock_comp.deadline = datetime.now(UTC) + timedelta(days=5)
    mock_comp.registration_url = None
    mock_comp.guidebook_url = None

    with patch(
        "cogs.competition_radar.list_active_competitions",
        new=AsyncMock(return_value=[mock_comp]),
    ):
        await cog.list_competitions.callback(
            cog,
            interaction,
            category=MagicMock(name="AI", value="AI"),
        )
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args[1]["embed"]
        assert "KSM AIoT Competition Radar" in embed.title
        assert "Hackathon AI 2026" in embed.fields[0].name


@pytest.mark.asyncio
async def test_lomba_detail():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()

    interaction = create_interaction()

    # Case 1: Not found
    with patch(
        "cogs.competition_radar.get_competition_by_id",
        new=AsyncMock(return_value=None),
    ):
        await cog.detail_competition.callback(cog, interaction, competition_id=999)
        interaction.response.send_message.assert_awaited_once()
        assert "not found" in interaction.response.send_message.call_args[0][0]

    # Case 2: Found
    interaction.response.send_message.reset_mock()
    mock_comp = MagicMock()
    mock_comp.id = 10
    mock_comp.name = "IoT Innovation Award"
    mock_comp.category = "IoT"
    mock_comp.deadline = datetime.now(UTC) + timedelta(days=12)
    mock_comp.description = "Lomba prototipe IoT"
    mock_comp.registration_url = "https://ieee.org/iot"
    mock_comp.guidebook_url = None
    mock_comp.target_role_id = None
    mock_comp.reminders_sent = [7]

    with patch(
        "cogs.competition_radar.get_competition_by_id",
        new=AsyncMock(return_value=mock_comp),
    ):
        await cog.detail_competition.callback(cog, interaction, competition_id=10)
        interaction.response.send_message.assert_awaited_once()
        embed = interaction.response.send_message.call_args[1]["embed"]
        assert "IoT Innovation Award" in embed.title


@pytest.mark.asyncio
async def test_lomba_delete():
    bot = MagicMock()
    cog = CompetitionRadar(bot)
    cog.deadline_reminder_loop.cancel()

    interaction = create_interaction(is_staff=True)

    with patch(
        "cogs.competition_radar.delete_competition",
        new=AsyncMock(return_value=True),
    ):
        await cog.delete_comp.callback(cog, interaction, competition_id=10)
        interaction.response.send_message.assert_awaited_once()
        assert "was removed" in interaction.response.send_message.call_args[0][0]
