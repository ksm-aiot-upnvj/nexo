import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from db.repositories.conversation import (
    reset_all_conversation_history,
    reset_conversation_history,
)
from db.repositories.scheduled_announcement import (
    cancel_scheduled_announcement,
    create_scheduled_announcement,
    get_due_scheduled_announcements,
    list_pending_announcements,
    mark_announcement_status,
)
from services.llm.client import check_llm_health
from services.mcp.client import MCP_SERVER_URL, get_tools_from_mcp_server
from utils.auth_helper import has_permission
from utils.command_help import build_command_help
from utils.message_cleanup import slow_purge

logger = logging.getLogger("core_commands")


class HelpSelect(discord.ui.Select):
    def __init__(self, parent_view):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label="Meta & Overview",
                description="General commands, latency, and status",
                emoji="🌟",
                value="0",
            ),
            discord.SelectOption(
                label="AI & LLM Commands",
                description="Slash commands & AI conversation controls",
                emoji="🤖",
                value="1",
            ),
            discord.SelectOption(
                label="IoT & MCP Tools",
                description="Live active tools connected via MCP Server",
                emoji="🧰",
                value="2",
            ),
            discord.SelectOption(
                label="Webhook & Deployment",
                description="GitHub Webhook Gateway & Auto-Deploy",
                emoji="🚀",
                value="3",
            ),
        ]
        super().__init__(
            placeholder="Select a category for more information...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.current_page = int(self.values[0])
        await self.parent_view.update_message(interaction)


class HelpView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.author_id = author_id
        self.current_page = 0
        self.total_pages = 4

        self.select_menu = HelpSelect(self)
        self.add_item(self.select_menu)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ Help menu ini dibuka oleh pengguna lain. Ketik `$help` untuk membuka menu Anda sendiri!",
                ephemeral=True,
            )
            return False
        return True

    async def fetch_mcp_tools_live(self):
        try:
            mcp_tools = await get_tools_from_mcp_server()
            if mcp_tools:
                self.bot.cached_mcp_tools = mcp_tools
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Live MCP retrieval failed during help view: {e}")

    async def build_embed(self) -> discord.Embed:
        embed = discord.Embed(color=discord.Color.blue())
        embed.set_author(
            name=f"Page {self.current_page + 1}/{self.total_pages}",
            icon_url=self.bot.user.display_avatar.url if self.bot.user else None,
        )

        if self.current_page == 0:
            embed.title = "🌟 Meta & General Commands"
            embed.description = (
                "Command umum Nexo KSM AIoT. Untuk cara pakai command tertentu, "
                "gunakan `$help <command>` atau `$help <command> <topik>`, "
                "misalnya `$help say to`.\n"
                "─────────────────────────────"
            )
            embed.add_field(
                name="Getting Started & Status",
                value=(
                    "`$help` / `/help` : Menampilkan panduan pengguna interaktif ini\n"
                    "`$help say to` : Contoh cara melihat bantuan per command/topik\n"
                    "`$ping` : Cek latensi bot ke Discord Gateway\n"
                    "`$status` : Cek status provider LLM dan MCP Server\n"
                    "`$tools` / `/tools` : Lihat tools Discord lokal dan MCP yang aktif"
                ),
                inline=False,
            )
            embed.add_field(
                name="Management & Moderation",
                value=(
                    "`$say to <#channel> <pesan>` : Kirim pesan atau pengumuman terjadwal\n"
                    "`$clear [limit]` : Hapus pesan percakapan secara massal\n"
                    "`$reset` / `/reset` : Hapus memory percakapanmu di channel ini"
                ),
                inline=False,
            )

        elif self.current_page == 1:
            embed.title = "🤖 AI & LLM Commands"
            embed.description = (
                "Fitur kecerdasan buatan terintegrasi provider OpenAI-compatible, MCP, dan Google Calendar.\n"
                "─────────────────────────────"
            )
            embed.add_field(
                name="Interactive AI Chat & Memory",
                value=(
                    "`/ask` or mention Nexo : Ask a question in natural language\n`/summarize` : Summarize recent channel messages within the safe context budget\n"
                    "`$reset` / `/reset` : Bersihkan memory di channel saat ini\n"
                    "`$tools` : Tampilkan seluruh tools lokal & MCP aktif\n"
                    "`/my-token-usage` : View your cumulative token totals\n"
                    "`/server-token-leaderboard` : View cumulative server usage rankings\n"
                    "`$tokenstats` and `$tokenleaderboard` : Prefix aliases for token usage\n"
                    "`$sync` : Sinkronisasi slash command (owner bot)"
                ),
                inline=False,
            )
            embed.add_field(
                name="Autonomous Capabilities",
                value=(
                    "Cukup mention/ajak ngobrol Nexo secara natural untuk:\n"
                    "• **Google Calendar & Discord Events:** Buat, sync 2 arah, klasifikasi & reminder agenda\n"
                    "• **Interactive Polls:** Buat polling interaktif & hitung hasil suara\n"
                    "• **Voice & Forum:** Buat thread forum & cek anggota di voice channel\n"
                    "• **Competitions:** Use `/competition add`, `/competition list`, `/competition detail`, and `/competition delete`"
                ),
                inline=False,
            )
            embed.add_field(
                name="Fitur Otomatis",
                value="- Menyambut anggota baru di channel *welcome*\n- Memberikan role otomatis saat perkenalan",
                inline=False,
            )

        elif self.current_page == 2:
            embed.title = "🧰 IoT & MCP Tools"
            embed.description = (
                "Seluruh Tools IoT & fungsi eksternal yang dapat diakses AI.\n"
                "─────────────────────────────"
            )
            await self.fetch_mcp_tools_live()

            def _clean_desc(raw_desc: str, max_len: int = 70) -> str:
                if not raw_desc:
                    return "Tidak ada deskripsi."
                first_line = (
                    raw_desc.strip()
                    .split("\n")[0]
                    .split("Args:")[0]
                    .strip()
                    .rstrip(".")
                )
                if len(first_line) > max_len:
                    return first_line[: max_len - 3] + "..."
                return first_line

            # 1. Local Tools
            local_tools = getattr(self.bot, "ai_tools", [])
            if local_tools:
                local_text = "\n".join(
                    [
                        f"• `{t.get('function', {}).get('name', 'Unknown')}` : {_clean_desc(t.get('function', {}).get('description', 'Local Discord tool'))}"
                        for t in local_tools
                    ]
                )
                embed.add_field(
                    name="⚙️ Local Discord Tools", value=local_text, inline=False
                )
            else:
                embed.add_field(
                    name="⚙️ Local Discord Tools",
                    value="*Tidak ada local tool.*",
                    inline=False,
                )

            # 2. Live MCP Tools
            mcp_tools = getattr(self.bot, "cached_mcp_tools", [])
            if mcp_tools:
                mcp_lines = [
                    f"• `{t.get('function', {}).get('name', 'Unknown')}` : {_clean_desc(t.get('function', {}).get('description', 'No description'))}"
                    for t in mcp_tools
                ]
                mcp_text = "\n".join(mcp_lines)
                if len(mcp_text) > 1024:
                    mcp_text = mcp_text[:1020] + "..."
                embed.add_field(
                    name="🌐 Live MCP External Tools (IoT)",
                    value=mcp_text,
                    inline=False,
                )
            else:
                embed.add_field(
                    name="🌐 Live MCP External Tools (IoT)",
                    value="*MCP Server offline atau belum memiliki tools terdaftar.*",
                    inline=False,
                )

        elif self.current_page == 3:
            embed.title = "🚀 Webhook & Auto-Deploy Engine"
            embed.description = (
                "Informasi integrasi GitHub Webhook Gateway & CD Pipeline.\n"
                "─────────────────────────────"
            )
            embed.add_field(
                name="FastAPI Webhook Gateway",
                value=(
                    "• **Endpoint:** `/nexo/webhook`\n"
                    "• **Authentication:** HMAC SHA-256 (`X-Hub-Signature-256`)\n"
                    "• **Routing:** Notifikasi commit & release dikirim ke channel tim sesuai `projects.json`"
                ),
                inline=False,
            )
            embed.add_field(
                name="Continuous Delivery (CD)",
                value=(
                    "• Webhook workflow tertentu dapat memicu redeploy melalui Portainer\n"
                    "• Notifikasi rilis resmi dan aktivitas project dikirim ke channel terkonfigurasi\n"
                    "• Pengelolaan endpoint dan secret dilakukan oleh operator; tidak ada command `/deploy`"
                ),
                inline=False,
            )

        embed.set_footer(
            text="Gunakan dropdown atau tombol di bawah untuk navigasi menu • Nexo KSM AIoT 🚀"
        )
        return embed

    async def update_message(self, interaction: discord.Interaction):
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="<<", style=discord.ButtonStyle.secondary, row=1)
    async def first_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = 0
        await self.update_message(interaction)

    @discord.ui.button(label="<", style=discord.ButtonStyle.primary, row=1)
    async def prev_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_message(interaction)

    @discord.ui.button(label=">", style=discord.ButtonStyle.primary, row=1)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self.update_message(interaction)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.secondary, row=1)
    async def last_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.current_page = self.total_pages - 1
        await self.update_message(interaction)

    @discord.ui.button(label="🔄 Refresh MCP", style=discord.ButtonStyle.success, row=1)
    async def refresh_mcp(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        await self.fetch_mcp_tools_live()
        embed = await self.build_embed()
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=embed, view=self
        )


WIB = timezone(timedelta(hours=7))


def parse_scheduled_time(text: str) -> tuple[datetime | None, str]:
    """
    Parses scheduling flags:
    1. --time "YYYY-MM-DD HH:MM" or --time "HH:MM" (WIB timezone, UTC+7)
    2. --in <digits><m|h|d> (relative delta e.g. --in 30m, --in 2h, --in 1d)
    Returns (scheduled_at_utc, cleaned_text)
    """
    now_utc = datetime.now(UTC)
    now_wib = now_utc.astimezone(WIB)
    scheduled_at: datetime | None = None

    # 1. Check for relative flag: --in 30m / --in 2h / --in 1d
    in_match = re.search(r"(?<!\S)--in\s+(\d+)([mhd])(?!\S)", text, re.IGNORECASE)
    if in_match:
        val = int(in_match.group(1))
        unit = in_match.group(2).lower()
        if unit == "m":
            scheduled_at = now_utc + timedelta(minutes=val)
        elif unit == "h":
            scheduled_at = now_utc + timedelta(hours=val)
        elif unit == "d":
            scheduled_at = now_utc + timedelta(days=val)
        text = text[: in_match.start()] + text[in_match.end() :]

    if not scheduled_at:
        # 2. Check for --time "YYYY-MM-DD HH:MM" or --time '...' or --time "HH:MM"
        time_match = re.search(
            r'(?<!\S)--time\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))(?!\S)',
            text,
            re.IGNORECASE,
        )
        if time_match:
            raw_time_str = (
                time_match.group(1) or time_match.group(2) or time_match.group(3)
            )
            parsed_dt = None
            for fmt in (
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d_%H:%M",
                "%d-%m-%Y %H:%M",
                "%Y/%m/%d %H:%M",
            ):
                try:
                    dt = datetime.strptime(raw_time_str, fmt)
                    parsed_dt = dt.replace(tzinfo=WIB)
                    break
                except ValueError:
                    pass

            # Try time only format (HH:MM)
            if not parsed_dt:
                try:
                    t_only = datetime.strptime(raw_time_str, "%H:%M").time()
                    cand = datetime.combine(now_wib.date(), t_only).replace(tzinfo=WIB)
                    if cand <= now_wib:
                        cand += timedelta(days=1)
                    parsed_dt = cand
                except ValueError:
                    pass

            if parsed_dt:
                scheduled_at = parsed_dt.astimezone(UTC)
                text = text[: time_match.start()] + text[time_match.end() :]

    text = re.sub(r" +", " ", text).strip()
    return scheduled_at, text


def parse_embed_flags(message_body: str) -> tuple[bool, str | None, str]:
    """
    Parses embed flags (--embed or -e) and title flags (--title "Title" or --title Title)
    from the message body.
    """
    is_embed = False
    title = None

    # Match --title "..." or --title '...' or --title word
    title_match = re.search(
        r'(?<!\S)--title\s+(?:"([^"]+)"|\'([^\']+)\'|(\S+))(?!\S)',
        message_body,
        re.IGNORECASE,
    )
    if title_match:
        is_embed = True
        title = title_match.group(1) or title_match.group(2) or title_match.group(3)
        message_body = (
            message_body[: title_match.start()] + message_body[title_match.end() :]
        )

    # Match --embed or -e
    embed_match = re.search(r"(?<!\S)(--embed|-e)(?!\S)", message_body, re.IGNORECASE)
    if embed_match:
        is_embed = True
        message_body = (
            message_body[: embed_match.start()] + message_body[embed_match.end() :]
        )

    # Normalize whitespace
    message_body = re.sub(r" +", " ", message_body).strip()
    return is_embed, title, message_body


def extract_pings(text: str) -> list[str]:
    """
    Extracts broadcast and specific mention tags (@everyone, @here, <@ID>, <@&ID>)
    so they can be placed in message content to trigger notifications when using embeds.
    """
    pings = re.findall(r"(@everyone|@here|<@!?[0-9]+>|<@&[0-9]+>)", text)
    return list(dict.fromkeys(pings))


async def resolve_smart_mentions(guild: discord.Guild, text: str) -> str:
    """
    Smart mention resolver for $say command:
    1. Converts role names (e.g. @Staff-Core, @"Admin") to <@&role_id>
    2. Converts snowflake IDs (e.g. @123456789012345678 or tag:123...) to <@ID> or <@&ID>
    3. Converts member names (e.g. @"User Name", @username) to <@member_id>
    """
    if not guild or not text:
        return text

    # 1. Resolve role names (longest first to avoid partial substring clashes)
    roles = sorted(
        [r for r in getattr(guild, "roles", []) if not r.is_default()],
        key=lambda r: len(r.name),
        reverse=True,
    )
    for role in roles:
        escaped_name = re.escape(role.name)
        pattern = rf'(?<!<)@(?:"{escaped_name}"|\'{escaped_name}\'|{escaped_name})\b'
        text = re.sub(pattern, role.mention, text, flags=re.IGNORECASE)
        if " " in role.name:
            variant1 = re.escape(role.name.replace(" ", "-"))
            variant2 = re.escape(role.name.replace(" ", "_"))
            pattern_v = rf"(?<!<)@(?:{variant1}|{variant2})\b"
            text = re.sub(pattern_v, role.mention, text, flags=re.IGNORECASE)

    # 2. Resolve Snowflake IDs: @123456789012345678 or id:123... or tag:123...
    def replace_id(match: re.Match) -> str:
        raw_id = match.group(1)
        s_id = int(raw_id)
        if hasattr(guild, "get_role") and guild.get_role(s_id):
            return f"<@&{raw_id}>"
        return f"<@{raw_id}>"

    text = re.sub(r"(?<!<)@(\d{6,20})\b", replace_id, text)
    text = re.sub(r"\b(?:id|tag):(\d{6,20})\b", replace_id, text, flags=re.IGNORECASE)

    # 3. Resolve Member Names: @"Member Name" or @username
    name_matches = list(
        re.finditer(r'(?<!<)@("([^"]+)"|\'([^\']+)\'|([A-Za-z0-9_\.\-]+))', text)
    )
    for m in reversed(name_matches):
        target_name = m.group(2) or m.group(3) or m.group(4)
        if not target_name:
            continue
        lower_name = target_name.lower()
        if lower_name in ("everyone", "here"):
            continue

        member = None
        for member_cand in getattr(guild, "members", []):
            if (
                member_cand.name.lower() == lower_name
                or member_cand.display_name.lower() == lower_name
                or (
                    getattr(member_cand, "global_name", None)
                    and member_cand.global_name.lower() == lower_name
                )
            ):
                member = member_cand
                break

        if not member and hasattr(guild, "query_members"):
            try:
                results = await guild.query_members(target_name, limit=1)
                if results:
                    member = results[0]
            except Exception:  # noqa: S110, BLE001
                pass

        if member:
            start, end = m.span()
            text = text[:start] + member.mention + text[end:]

    return text


class CoreCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_scheduled_announcements.start()

    def cog_unload(self):
        self.check_scheduled_announcements.cancel()

    @tasks.loop(seconds=20)
    async def check_scheduled_announcements(self):
        """Checks and delivers due scheduled announcements."""
        try:
            now_utc = datetime.now(UTC)
            due_announcements = await get_due_scheduled_announcements(now_utc)
            for item in due_announcements:
                guild = self.bot.get_guild(item.guild_id)
                if not guild:
                    continue
                channel = guild.get_channel(item.channel_id)
                if not channel:
                    await mark_announcement_status(item.id, "failed")
                    continue

                try:
                    allowed_mentions = discord.AllowedMentions(
                        everyone=True, roles=True, users=True
                    )
                    final_body = await resolve_smart_mentions(guild, item.content)

                    if item.is_embed:
                        pings = extract_pings(final_body)
                        ping_content = " ".join(pings) if pings else None
                        embed = discord.Embed(
                            title=item.title,
                            description=final_body,
                            color=discord.Color.from_rgb(0, 168, 252),
                        )
                        footer_text = f"Pengumuman • {guild.name}"
                        if getattr(guild, "icon", None):
                            embed.set_footer(text=footer_text, icon_url=guild.icon.url)
                        else:
                            embed.set_footer(text=footer_text)

                        await channel.send(
                            content=ping_content,
                            embed=embed,
                            allowed_mentions=allowed_mentions,
                        )
                    else:
                        await channel.send(
                            final_body,
                            allowed_mentions=allowed_mentions,
                        )

                    await mark_announcement_status(item.id, "sent")
                    logger.info(
                        f"Delivered scheduled announcement {item.id} to #{channel.name}"
                    )
                except Exception as send_err:  # noqa: BLE001
                    logger.error(
                        f"Failed to deliver scheduled announcement {item.id}: {send_err}"
                    )
                    await mark_announcement_status(item.id, "failed")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error in check_scheduled_announcements loop: {e}")

    @check_scheduled_announcements.before_loop
    async def before_announcements(self):
        try:
            await self.bot.wait_until_ready()
        except RuntimeError, asyncio.CancelledError:
            pass

    @commands.command()
    async def ping(self, ctx):
        """Responds with the bot's latency."""
        await ctx.send(f"Pong! Latency: {round(self.bot.latency * 1000)}ms")

    @commands.command(name="help", aliases=["h", "guide"])
    async def help(self, ctx, *, query: str | None = None):
        """Displays the Nexo bot usage guide with interactive UI."""
        if query and query.strip():
            return await ctx.send(embed=build_command_help(self.bot, query))
        view = HelpView(self.bot, ctx.author.id)
        embed = await view.build_embed()
        await ctx.send(embed=embed, view=view)

    @app_commands.command(
        name="help", description="Open Nexo's interactive command guide."
    )
    @app_commands.describe(command="Command or topic to look up, such as `say to`.")
    async def help_slash(
        self, interaction: discord.Interaction, command: str | None = None
    ):
        if command and command.strip():
            await interaction.response.send_message(
                embed=build_command_help(self.bot, command), ephemeral=True
            )
            return
        view = HelpView(self.bot, interaction.user.id)
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view)

    @commands.command(name="tools", aliases=["mcp", "tool"])
    async def tools(self, ctx):
        """List all available tools with live MCP retrieval."""
        view = HelpView(self.bot, ctx.author.id)
        view.current_page = 2  # Jump to MCP Tools page directly
        embed = await view.build_embed()
        await ctx.send(embed=embed, view=view)

    @app_commands.command(
        name="tools", description="List active local Discord and MCP tools."
    )
    async def tools_slash(self, interaction: discord.Interaction):
        view = HelpView(self.bot, interaction.user.id)
        view.current_page = 2
        embed = await view.build_embed()
        await interaction.response.send_message(embed=embed, view=view)

    @commands.command()
    async def status(self, ctx):
        """Check the status of services."""
        status_messages = ["🔍 Checking service status..."]

        # Check LLM provider
        is_llm_healthy, _ = await check_llm_health()
        if is_llm_healthy:
            status_messages.append("✅ LLM Provider: ONLINE")
        else:
            status_messages.append("❌ LLM Provider: UNAVAILABLE")
        # Check MCP server
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{MCP_SERVER_URL}/", timeout=aiohttp.ClientTimeout(total=5)
                ) as response,
            ):
                if response.status == 200:
                    status_messages.append("✅ MCP Server: ONLINE")
                else:
                    status_messages.append("❌ MCP Server: UNAVAILABLE")
        except TimeoutError, aiohttp.ClientError:
            status_messages.append("❌ MCP Server: UNAVAILABLE")

        await ctx.send("\n".join(status_messages))

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx):
        """Syncs global slash commands and clears local guild duplicates."""
        await ctx.send("🔄 Clearing guild duplicates and syncing globally...")

        # 1. Clear guild-specific commands to avoid duplicates
        self.bot.tree.clear_commands(guild=ctx.guild)
        await self.bot.tree.sync(guild=ctx.guild)

        # 2. Sync globally
        synced = await self.bot.tree.sync()
        await ctx.send(f"✅ Successfully synced {len(synced)} global slash commands!")

    @commands.command()
    async def clear(self, ctx, limit: int = 100):
        """Deletes messages in bulk or one by one with a delay."""
        if not ctx.author.guild_permissions.manage_messages:
            return await ctx.send("❌ You do not have Manage Messages permission.")

        await ctx.send(f"🧹 Starting deletion of {limit} messages...")

        try:
            deleted = await slow_purge(ctx.channel, limit=limit + 2)
            await ctx.send(
                f"✅ Successfully deleted {len(deleted)} messages!", delete_after=3
            )
        except discord.Forbidden:
            await ctx.send("❌ Bot does not have Manage Messages permission.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ HTTP Error occurred: {e}")

    @commands.command(aliases=["resetcontext", "clearcontext"])
    async def reset(self, ctx, mode: str = "user"):
        """Resets the AI conversation history and memory context."""
        if mode.lower() == "all":
            if not ctx.author.guild_permissions.manage_messages:
                return await ctx.send(
                    "❌ You need 'Manage Messages' permission to reset all conversation context."
                )
            await reset_all_conversation_history()
            if hasattr(self.bot, "conversation_history"):
                self.bot.conversation_history.clear()
            msg = "Global AI conversation memory context has been cleared for all users! 🧹✨"
        else:
            await reset_conversation_history(ctx.author.id, ctx.channel.id)
            if hasattr(self.bot, "conversation_history"):
                self.bot.conversation_history.pop(ctx.author.id, None)
            msg = (
                f"AI conversation memory context cleared for <@{ctx.author.id}> "
                f"in this channel! 🧹✨"
            )

        embed = discord.Embed(
            title="🧠 Context Reset",
            description=msg,
            color=discord.Color.blue(),
        )
        await ctx.send(embed=embed)

    @app_commands.command(
        name="reset", description="Clear your AI conversation context memory"
    )
    async def reset_slash(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        await reset_conversation_history(user_id, interaction.channel_id)
        if hasattr(self.bot, "conversation_history"):
            self.bot.conversation_history.pop(user_id, None)

        embed = discord.Embed(
            title="🧠 Context Reset",
            description=(
                f"AI conversation memory context cleared for <@{user_id}> "
                "in this channel! 🧹✨"
            ),
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name="voice", aliases=["vc", "voicechannel"])
    async def voice_cmd(self, ctx, *, channel_name: str | None = None):
        """Get list of users currently in a voice channel."""
        target_vc = None

        if channel_name and channel_name.strip():
            c_name = channel_name.lower().strip()
            for vc in ctx.guild.voice_channels:
                if c_name in vc.name.lower():
                    target_vc = vc
                    break

        if not target_vc and isinstance(
            ctx.channel, (discord.VoiceChannel, discord.StageChannel)
        ):
            target_vc = ctx.channel

        if not target_vc and ctx.author.voice and ctx.author.voice.channel:
            target_vc = ctx.author.voice.channel

        if not target_vc:
            if channel_name:
                return await ctx.send(
                    f"Voice Channel containing '{channel_name}' was not found."
                )
            return await ctx.send(
                "Please specify a voice channel name, join a voice channel, or run this command inside a voice channel text chat."
            )

        members = target_vc.members
        embed = discord.Embed(
            title=f"🔊 Voice Channel: {target_vc.name}",
            color=discord.Color.blue(),
        )

        if not members:
            embed.description = "Currently, there is no one in this Voice Channel."
        else:
            member_list = [f"• {m.mention} ({m.display_name})" for m in members]
            embed.description = f"**Total Members:** {len(members)}\n\n" + "\n".join(
                member_list
            )

        embed.set_footer(text=f"Server: {ctx.guild.name} • Nexo KSM AIoT")
        await ctx.send(embed=embed)

    @app_commands.command(
        name="voice", description="Check current members in a voice channel"
    )
    @app_commands.describe(channel_name="Name of the voice channel to check (optional)")
    async def voice_slash(
        self, interaction: discord.Interaction, channel_name: str | None = None
    ):
        target_vc = None

        if channel_name and channel_name.strip():
            c_name = channel_name.lower().strip()
            for vc in interaction.guild.voice_channels:
                if c_name in vc.name.lower():
                    target_vc = vc
                    break

        if not target_vc and isinstance(
            interaction.channel, (discord.VoiceChannel, discord.StageChannel)
        ):
            target_vc = interaction.channel

        if not target_vc and interaction.user.voice and interaction.user.voice.channel:
            target_vc = interaction.user.voice.channel

        if not target_vc:
            if channel_name:
                return await interaction.response.send_message(
                    f"Voice Channel containing '{channel_name}' was not found."
                )
            return await interaction.response.send_message(
                "Please specify a voice channel name, join a voice channel, or run this command inside a voice channel text chat."
            )

        members = target_vc.members
        embed = discord.Embed(
            title=f"🔊 Voice Channel: {target_vc.name}",
            color=discord.Color.blue(),
        )

        if not members:
            embed.description = "Currently, there is no one in this Voice Channel."
        else:
            member_list = [f"• {m.mention} ({m.display_name})" for m in members]
            embed.description = f"**Total Members:** {len(members)}\n\n" + "\n".join(
                member_list
            )

        embed.set_footer(text=f"Server: {interaction.guild.name} • Nexo KSM AIoT")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="mention-member",
        description="Post a message in this channel mentioning one selected member.",
    )
    @app_commands.describe(
        target="The member to mention.",
        message="The message to post in this channel.",
    )
    @app_commands.checks.cooldown(
        1,
        10.0,
        key=lambda interaction: (interaction.guild_id or 0, interaction.user.id),
    )
    async def mention_member(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        message: str,
    ):
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message(
                "❌ Command ini hanya dapat digunakan di dalam server.",
                ephemeral=True,
            )
            return
        if not interaction.permissions.send_messages:
            await interaction.response.send_message(
                "❌ Anda tidak memiliki izin mengirim pesan di channel ini.",
                ephemeral=True,
            )
            return
        if not message.strip():
            await interaction.response.send_message(
                "❌ Pesan tidak boleh kosong.",
                ephemeral=True,
            )
            return
        if "@everyone" in message.casefold() or "@here" in message.casefold():
            await interaction.response.send_message(
                "❌ Mention @everyone dan @here tidak diperbolehkan.",
                ephemeral=True,
            )
            return

        try:
            await interaction.channel.send(
                content=f"{target.mention} {message.strip()}",
                allowed_mentions=discord.AllowedMentions(
                    users=[target],
                    everyone=False,
                    roles=False,
                    replied_user=False,
                ),
            )
            await interaction.response.send_message(
                f"✅ Pesan dikirim kepada {target.display_name}.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Nexo tidak memiliki izin mengirim pesan di channel ini.",
                ephemeral=True,
            )
        except discord.HTTPException:
            logger.exception("Failed to send /mention-member message")
            await interaction.response.send_message(
                "❌ Pesan gagal dikirim karena Discord sedang bermasalah.",
                ephemeral=True,
            )

    @commands.command(name="say")
    async def say(self, ctx, *, text: str | None = None):
        """
        Sends a message to a destination channel on behalf of the bot.
        Usage:
          $say to #channel <message>
          $say to #channel --embed <message>
          $say to #channel --embed --title "Judul" <message>
        """
        if not has_permission(
            ctx,
            "manage_messages",
            allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin"],
        ):
            return await ctx.send(
                "❌ **Akses Ditolak**: Anda tidak memiliki izin untuk menggunakan perintah `$say`.",
                delete_after=5,
            )

        if not text or not text.strip():
            return await ctx.send(
                "ℹ️ **Format Penggunaan:**\n"
                "• Kirim sekarang: `$say to #channel <pesan>`\n"
                '• Kirim Embed: `$say to #channel --embed [--title "Judul"] <pesan>`\n'
                '• Jadwalkan: `$say to #channel --time "YYYY-MM-DD HH:MM" <pesan>` atau `--in <30m|2h|1d>`\n'
                "• Cek antrean jadwal: `$say list`\n"
                "• Batalkan jadwal: `$say cancel <ID>`",
                delete_after=12,
            )

        content = text.strip()

        # Handle '$say list'
        if content.lower() == "list":
            pending = await list_pending_announcements(ctx.guild.id)
            if not pending:
                return await ctx.send(
                    "ℹ️ Tidak ada pengumuman yang sedang menunggu jadwal.",
                    delete_after=8,
                )
            embed = discord.Embed(
                title="⏰ Antrean Pengumuman Terjadwal",
                color=discord.Color.blue(),
            )
            for idx, a in enumerate(pending[:10], start=1):
                ch = ctx.guild.get_channel(a.channel_id)
                ch_mention = ch.mention if ch else f"`{a.channel_id}`"
                ts = int(a.scheduled_at.timestamp())
                snippet = a.content[:80] + "..." if len(a.content) > 80 else a.content
                embed.add_field(
                    name=f"{idx}. ID: `{a.id}`",
                    value=(
                        f"• **Channel:** {ch_mention}\n"
                        f"• **Waktu:** <t:{ts}:F> (<t:{ts}:R>)\n"
                        f"• **Format:** {'Embed' if a.is_embed else 'Teks Biasa'}\n"
                        f"• **Pesan:** {snippet}"
                    ),
                    inline=False,
                )
            return await ctx.send(embed=embed)

        # Handle '$say cancel <id>'
        if content.lower().startswith("cancel "):
            raw_id = content.split(maxsplit=1)[1].strip()
            try:
                announcement_uuid = uuid.UUID(raw_id)
                success = await cancel_scheduled_announcement(
                    announcement_uuid, ctx.guild.id
                )
                if success:
                    return await ctx.send(
                        f"✅ Pengumuman dengan ID `{raw_id}` berhasil dibatalkan.",
                        delete_after=8,
                    )
                else:
                    return await ctx.send(
                        f"❌ Pengumuman dengan ID `{raw_id}` tidak ditemukan atau sudah dikirim.",
                        delete_after=8,
                    )
            except ValueError:
                return await ctx.send(
                    "❌ Format ID tidak valid. Harap gunakan format UUID yang benar.",
                    delete_after=6,
                )

        # Handle 'to' prefix if present
        if content.lower().startswith("to "):
            content = content[3:].strip()

        # Parse channel and message
        parts = content.split(maxsplit=1)
        if not parts or len(parts) < 2:
            return await ctx.send(
                "⚠️ Mohon sertakan channel tujuan dan isi pesan!\nContoh: `$say to #pengumuman Selamat sore semua!`",
                delete_after=8,
            )

        channel_identifier, message_body = parts[0], parts[1].strip()
        if not message_body:
            return await ctx.send("⚠️ Pesan tidak boleh kosong!", delete_after=5)

        # Parse embed flags and options
        is_embed, title, message_body = parse_embed_flags(message_body)

        # Smart Mention Resolution
        message_body = await resolve_smart_mentions(ctx.guild, message_body)

        if not message_body:
            return await ctx.send("⚠️ Pesan tidak boleh kosong!", delete_after=5)

        # Resolve channel: mention (<#id>), ID, or channel name
        target_channel = None
        cleaned_id = re.sub(r"[<#>]", "", channel_identifier)
        if cleaned_id.isdigit():
            target_channel = ctx.guild.get_channel(int(cleaned_id))

        if not target_channel:
            c_name = channel_identifier.lower().lstrip("#")
            for ch in ctx.guild.text_channels:
                if ch.name.lower() == c_name:
                    target_channel = ch
                    break

        if not target_channel:
            return await ctx.send(
                f"❌ Channel `{channel_identifier}` tidak ditemukan di server ini.",
                delete_after=6,
            )

        # Check for scheduling flags (--time or --in)
        scheduled_at, message_body = parse_scheduled_time(message_body)
        if scheduled_at:
            now_utc = datetime.now(UTC)
            if scheduled_at <= now_utc:
                return await ctx.send(
                    "⚠️ Waktu jadwal harus berada di masa depan!", delete_after=6
                )

            # Try deleting invocation message
            try:
                await ctx.message.delete()
            except Exception:  # noqa: S110, BLE001
                pass

            announcement = await create_scheduled_announcement(
                guild_id=ctx.guild.id,
                channel_id=target_channel.id,
                author_id=ctx.author.id,
                content=message_body,
                scheduled_at=scheduled_at,
                is_embed=is_embed,
                title=title,
            )
            ts = int(scheduled_at.timestamp())
            return await ctx.send(
                f"✅ **Pengumuman Berhasil Dijadwalkan!**\n"
                f"• **Channel Tujuan:** {target_channel.mention}\n"
                f"• **Waktu Pengiriman:** <t:{ts}:F> (<t:{ts}:R>)\n"
                f"• **Format:** {'Embed' if is_embed else 'Teks Biasa'}\n"
                f"• **ID:** `{announcement.id}` (Gunakan `$say cancel {announcement.id}` jika ingin membatalkan)",
                delete_after=15,
            )

        try:
            # Delete invocation message if possible
            try:
                await ctx.message.delete()
            except Exception:  # noqa: S110, BLE001
                pass

            allowed_mentions = discord.AllowedMentions(
                everyone=True, roles=True, users=True
            )

            if is_embed:
                pings = extract_pings(message_body)
                ping_content = " ".join(pings) if pings else None

                embed = discord.Embed(
                    title=title,
                    description=message_body,
                    color=discord.Color.from_rgb(0, 168, 252),
                )
                footer_text = f"Pengumuman • {ctx.guild.name}"
                if getattr(ctx.guild, "icon", None):
                    embed.set_footer(text=footer_text, icon_url=ctx.guild.icon.url)
                else:
                    embed.set_footer(text=footer_text)

                await target_channel.send(
                    content=ping_content,
                    embed=embed,
                    allowed_mentions=allowed_mentions,
                )
            else:
                await target_channel.send(
                    message_body,
                    allowed_mentions=allowed_mentions,
                )

            logger.info(
                f"User {ctx.author.name} (ID: {ctx.author.id}) used $say to #{target_channel.name} (embed={is_embed})"
            )
        except discord.Forbidden:
            await ctx.send(
                f"❌ Bot tidak memiliki izin mengirim pesan di {target_channel.mention}."
            )
        except Exception as e:  # noqa: BLE001
            await ctx.send(f"❌ Gagal mengirim pesan: {e}")


async def setup(bot):
    await bot.add_cog(CoreCommands(bot))
