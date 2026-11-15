import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db.repositories.competition import (
    create_competition,
    delete_competition,
    get_competition_by_id,
    list_active_competitions,
    update_competition_reminders,
)
from utils.auth_helper import has_permission

logger = logging.getLogger("competition_radar")

WIB = timezone(timedelta(hours=7))

CATEGORIES = [
    app_commands.Choice(name="🤖 Artificial Intelligence (AI)", value="AI"),
    app_commands.Choice(name="📡 Internet of Things (IoT)", value="IoT"),
    app_commands.Choice(name="💻 Web & Mobile Development", value="Web/Mobile"),
    app_commands.Choice(name="📊 Data Science & Analytics", value="Data Science"),
    app_commands.Choice(name="🏆 Gemastik / PKM / National", value="Gemastik/PKM"),
    app_commands.Choice(name="🌐 General & Other", value="Umum"),
]


def parse_deadline_wib(deadline_str: str) -> datetime | None:
    """
    Parses deadline string assumed in WIB (UTC+7):
    Supports 'YYYY-MM-DD HH:MM', 'YYYY-MM-DD', 'DD-MM-YYYY HH:MM', 'DD-MM-YYYY'.
    Returns UTC datetime.
    """
    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d_%H:%M",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
    )
    for fmt in formats:
        try:
            dt = datetime.strptime(deadline_str.strip(), fmt)
            if fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                # Default end of day: 23:59 WIB
                dt = dt.replace(hour=23, minute=59, second=59)
            dt_wib = dt.replace(tzinfo=WIB)
            return dt_wib.astimezone(UTC)
        except ValueError:
            pass
    return None


class CompetitionView(discord.ui.View):
    def __init__(self, reg_url: str | None = None, guide_url: str | None = None):
        super().__init__(timeout=None)
        if reg_url:
            self.add_item(
                discord.ui.Button(
                    label="📝 Link Pendaftaran",
                    url=reg_url,
                    style=discord.ButtonStyle.link,
                )
            )
        if guide_url:
            self.add_item(
                discord.ui.Button(
                    label="📖 Buku Panduan",
                    url=guide_url,
                    style=discord.ButtonStyle.link,
                )
            )


class CompetitionRadar(commands.Cog):
    """Cog for managing competitions, hackathons, and deadline countdown reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.deadline_reminder_loop.start()

    def cog_unload(self):
        self.deadline_reminder_loop.cancel()

    competition_group = app_commands.Group(
        name="competition",
        description="Manage competitions, hackathons, and deadline reminders.",
    )

    @competition_group.command(
        name="add",
        description="Add a competition or hackathon to the server radar.",
    )
    @app_commands.choices(category=CATEGORIES)
    @app_commands.describe(
        name="Competition or hackathon name, for example Gemastik XIX 2026.",
        deadline="Registration deadline in WIB, for example 2026-10-15 23:59.",
        category="Competition category.",
        registration_url="Optional registration page URL.",
        guide_url="Optional guidebook URL.",
        details="Optional summary, requirements, or theme.",
        channel="Channel for deadline reminders; defaults to this channel.",
        role="Optional role to mention in deadline reminders.",
    )
    async def add_competition(
        self,
        interaction: discord.Interaction,
        name: str,
        deadline: str,
        category: app_commands.Choice[str],
        registration_url: str | None = None,
        guide_url: str | None = None,
        details: str | None = None,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ):
        if not has_permission(
            interaction,
            "manage_messages",
            allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin"],
        ):
            return await interaction.response.send_message(
                "❌ You do not have permission to add a competition.",
                ephemeral=True,
            )

        deadline_utc = parse_deadline_wib(deadline)
        if not deadline_utc:
            return await interaction.response.send_message(
                "❌ Invalid deadline format. Use `YYYY-MM-DD HH:MM` or `YYYY-MM-DD` in WIB.",
                ephemeral=True,
            )

        now_utc = datetime.now(UTC)
        if deadline_utc <= now_utc:
            return await interaction.response.send_message(
                "⚠️ The deadline must be in the future.",
                ephemeral=True,
            )

        target_channel = channel or interaction.channel
        comp = await create_competition(
            guild_id=interaction.guild_id,
            name=name.strip(),
            category=category.value,
            deadline=deadline_utc,
            channel_id=target_channel.id,
            created_by=interaction.user.id,
            registration_url=registration_url.strip() if registration_url else None,
            guidebook_url=guide_url.strip() if guide_url else None,
            description=details.strip() if details else None,
            target_role_id=role.id if role else None,
        )

        ts = int(deadline_utc.timestamp())
        embed = discord.Embed(
            title=f"🏆 Competition Added: {comp.name}",
            description=comp.description or "*No additional details provided.*",
            color=discord.Color.gold(),
        )
        embed.add_field(name="📂 Category", value=comp.category, inline=True)
        embed.add_field(
            name="⏰ Registration Deadline",
            value=f"<t:{ts}:F>\n(<t:{ts}:R>)",
            inline=True,
        )
        embed.add_field(
            name="📢 Reminder Channel",
            value=target_channel.mention,
            inline=True,
        )
        if role:
            embed.add_field(name="🎯 Mentioned Role", value=role.mention, inline=True)
        embed.set_footer(
            text=f"Competition ID: {comp.id} • Added by {interaction.user.display_name}"
        )

        view = CompetitionView(comp.registration_url, comp.guidebook_url)
        await interaction.response.send_message(embed=embed, view=view)

    @competition_group.command(
        name="list",
        description="List active competitions and hackathons.",
    )
    @app_commands.choices(category=CATEGORIES)
    @app_commands.describe(category="Optionally filter by category.")
    async def list_competitions(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str] | None = None,
    ):
        competitions = await list_active_competitions(interaction.guild_id)
        if category:
            competitions = [c for c in competitions if c.category == category.value]

        if not competitions:
            filter_text = f" for category `{category.name}`" if category else ""
            return await interaction.response.send_message(
                f"ℹ️ No active competitions are currently listed{filter_text}.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="📡 KSM AIoT Competition Radar",
            description="Technology competitions that are currently accepting registrations:",
            color=discord.Color.from_rgb(0, 168, 252),
        )

        for comp in competitions[:12]:
            ts = int(comp.deadline.timestamp())
            links = []
            if comp.registration_url:
                links.append(f"[Register]({comp.registration_url})")
            if comp.guidebook_url:
                links.append(f"[Guidebook]({comp.guidebook_url})")
            link_str = " • ".join(links) if links else "No links available"

            embed.add_field(
                name=f"#{comp.id} | {comp.name} [{comp.category}]",
                value=f"• **Deadline:** <t:{ts}:R> (<t:{ts}:d>)\n• **Info:** {link_str}",
                inline=False,
            )

        embed.set_footer(
            text=f"{len(competitions)} active competitions • Use /competition detail to view an entry"
        )
        await interaction.response.send_message(embed=embed)

    @competition_group.command(
        name="detail",
        description="Show the details of a competition.",
    )
    @app_commands.describe(competition_id="ID of the competition to view.")
    async def detail_competition(
        self, interaction: discord.Interaction, competition_id: int
    ):
        comp = await get_competition_by_id(competition_id, interaction.guild_id)
        if not comp or not comp.is_active:
            return await interaction.response.send_message(
                f"❌ Competition `#{competition_id}` was not found or is closed.",
                ephemeral=True,
            )

        ts = int(comp.deadline.timestamp())
        ch = interaction.guild.get_channel(comp.channel_id)
        ch_text = ch.mention if ch else f"`{comp.channel_id}`"

        embed = discord.Embed(
            title=f"🏆 {comp.name}",
            description=comp.description or "*No additional details provided.*",
            color=discord.Color.blue(),
        )
        embed.add_field(name="📂 Category", value=comp.category, inline=True)
        embed.add_field(name="📢 Reminder Channel", value=ch_text, inline=True)
        embed.add_field(
            name="⏰ Deadline",
            value=f"<t:{ts}:F>\n**Time remaining:** <t:{ts}:R>",
            inline=False,
        )

        if comp.target_role_id:
            role = interaction.guild.get_role(comp.target_role_id)
            if role:
                embed.add_field(name="🎯 Role", value=role.mention, inline=True)

        reminders_text = (
            ", ".join(f"H-{r}" for r in sorted(comp.reminders_sent, reverse=True))
            if comp.reminders_sent
            else "Belum ada"
        )
        embed.add_field(name="🔔 Reminders Sent", value=reminders_text, inline=True)
        embed.set_footer(text=f"Competition ID: {comp.id} • KSM AIoT Competition Radar")

        view = CompetitionView(comp.registration_url, comp.guidebook_url)
        await interaction.response.send_message(embed=embed, view=view)

    @competition_group.command(
        name="delete",
        description="Remove a competition from the radar (staff only).",
    )
    @app_commands.describe(competition_id="ID of the competition to remove.")
    async def delete_comp(self, interaction: discord.Interaction, competition_id: int):
        if not has_permission(
            interaction,
            "manage_messages",
            allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin"],
        ):
            return await interaction.response.send_message(
                "❌ You do not have permission to remove a competition.",
                ephemeral=True,
            )

        success = await delete_competition(competition_id, interaction.guild_id)
        if success:
            await interaction.response.send_message(
                f"✅ Competition `#{competition_id}` was removed from the radar.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ Competition `#{competition_id}` was not found.",
                ephemeral=True,
            )

    @tasks.loop(minutes=30)
    async def deadline_reminder_loop(self):
        """Background task checking competition deadlines every 30 minutes."""
        try:
            now_utc = datetime.now(UTC)
            for guild in self.bot.guilds:
                competitions = await list_active_competitions(guild.id)
                for comp in competitions:
                    delta = comp.deadline - now_utc
                    total_seconds = delta.total_seconds()

                    # Auto-close expired competitions
                    if total_seconds <= 0:
                        await delete_competition(comp.id, guild.id)
                        logger.info(
                            f"Auto-closed expired competition #{comp.id} ({comp.name})"
                        )
                        continue

                    days_left = total_seconds / 86400.0
                    sent_list = list(comp.reminders_sent or [])

                    # Reminder milestones: H-1, H-3, H-7
                    target_milestone = None
                    if days_left <= 1.0 and 1 not in sent_list:
                        target_milestone = 1
                    elif days_left <= 3.0 and 3 not in sent_list:
                        target_milestone = 3
                    elif days_left <= 7.0 and 7 not in sent_list:
                        target_milestone = 7

                    if target_milestone is not None:
                        channel = guild.get_channel(comp.channel_id)
                        if channel:
                            ts = int(comp.deadline.timestamp())
                            role_mention = (
                                f"<@&{comp.target_role_id}>"
                                if comp.target_role_id
                                else None
                            )

                            if target_milestone == 1:
                                color = discord.Color.red()
                                alert_prefix = "🚨 **[H-1 LAST CALL DEADLINE LOMBA]**"
                            elif target_milestone == 3:
                                color = discord.Color.orange()
                                alert_prefix = "⚠️ **[H-3 REMINDER DEADLINE LOMBA]**"
                            else:
                                color = discord.Color.gold()
                                alert_prefix = "📢 **[H-7 REMINDER PERSIAPAN LOMBA]**"

                            embed = discord.Embed(
                                title=f"{alert_prefix} {comp.name}",
                                description=(
                                    f"Batas akhir pendaftaran tersisa **{target_milestone} hari lagi**!\n"
                                    f"Pastikan berkas proposal, tim, dan administrasi telah dipersiapkan.\n\n"
                                    f"⏰ **Batas Waktu:** <t:{ts}:F> (<t:{ts}:R>)\n"
                                    f"📂 **Kategori:** `{comp.category}`"
                                ),
                                color=color,
                            )
                            embed.set_footer(
                                text=f"ID Lomba: {comp.id} • KSM AIoT Competition Radar"
                            )
                            view = CompetitionView(
                                comp.registration_url, comp.guidebook_url
                            )

                            allowed_mentions = discord.AllowedMentions(
                                roles=True, everyone=True, users=True
                            )
                            await channel.send(
                                content=role_mention,
                                embed=embed,
                                view=view,
                                allowed_mentions=allowed_mentions,
                            )

                            sent_list.append(target_milestone)
                            await update_competition_reminders(comp.id, sent_list)
                            logger.info(
                                f"Sent H-{target_milestone} reminder for competition #{comp.id} ({comp.name})"
                            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error in deadline_reminder_loop: {e}")

    @deadline_reminder_loop.before_loop
    async def before_reminder_loop(self):
        try:
            await self.bot.wait_until_ready()
        except RuntimeError, asyncio.CancelledError:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(CompetitionRadar(bot))
