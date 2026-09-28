import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from db.repository import (
    check_and_trigger_rolling_summary,
    count_token,
    get_guild_token_leaderboard,
    get_user_token_stats,
)
from utils.mcp_client import (
    SYSTEM_PROMPT,
    ai_client,
    check_services_health,
    get_tools_from_mcp_server,
    process_with_mcp_tools,
    sanitize_tools_list,
)

logger = logging.getLogger("agent_orchestrator")


class AgentOrchestrator(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.worker_task = None
        self._mention_cooldowns: dict[int, float] = {}

    async def cog_load(self):
        # Ensure queue and lock exist on bot
        if not hasattr(self.bot, "message_queue"):
            self.bot.message_queue = asyncio.Queue()
        if not hasattr(self.bot, "bot_lock"):
            self.bot.bot_lock = asyncio.Lock()

        # Load tools on startup
        try:
            if not hasattr(self.bot, "cached_mcp_tools"):
                self.bot.cached_mcp_tools = await get_tools_from_mcp_server()
        except Exception as e:
            logger.warning(f"MCP Server not available on startup: {e}")

        # Pre-calculate static base token overhead once
        try:
            all_tools = []
            if getattr(self.bot, "ai_tools", None):
                all_tools.extend(self.bot.ai_tools)
            if getattr(self.bot, "cached_mcp_tools", None):
                all_tools.extend(self.bot.cached_mcp_tools)

            sanitized = sanitize_tools_list(all_tools) if all_tools else []
            tools_json_str = json.dumps(sanitized)

            sys_tok = await count_token(SYSTEM_PROMPT)
            tools_tok = await count_token(tools_json_str)
            self.bot.base_overhead_tokens = sys_tok + tools_tok
            logger.info(
                f"Pre-calculated Base Overhead: {self.bot.base_overhead_tokens} tokens "
                f"(System: {sys_tok}, Tools: {tools_tok})"
            )
        except Exception as e:
            self.bot.base_overhead_tokens = 1400
            logger.warning(f"Could not pre-calculate base token overhead: {e}")

        # Start worker
        self.worker_task = asyncio.create_task(self.worker())
        logger.info("Agent Orchestrator Worker started.")

    async def cog_unload(self):
        if self.worker_task:
            self.worker_task.cancel()

    async def worker(self):
        """Constantly watches the queue and processes requests one by one with guaranteed lifecycle and recovery"""
        while True:
            try:
                queue_item = await self.bot.message_queue.get()
            except asyncio.CancelledError:
                logger.info("Agent Orchestrator Worker shutting down gracefully.")
                break

            try:
                queue_status_msg = None
                if len(queue_item) == 4:
                    ctx_obj, prompt, queued_time, queue_status_msg = queue_item
                elif len(queue_item) == 3:
                    ctx_obj, prompt, queued_time = queue_item
                else:
                    ctx_obj, prompt = queue_item
                    queued_time = time.time()

                is_interaction = isinstance(ctx_obj, discord.Interaction)
                user_id = ctx_obj.user.id if is_interaction else ctx_obj.author.id
                user_name = ctx_obj.user.name if is_interaction else ctx_obj.author.name
                channel = ctx_obj.channel

                async with self.bot.bot_lock:
                    is_expired = (time.time() - queued_time) > 850
                    wait_msg = None

                    if not is_expired:
                        try:
                            if is_interaction:
                                await ctx_obj.edit_original_response(
                                    content="*Nexo is thinking... 💭*"
                                )
                            elif queue_status_msg:
                                wait_msg = await queue_status_msg.edit(
                                    content="*Nexo is thinking... 💭*"
                                )
                            else:
                                wait_msg = await ctx_obj.reply(
                                    "*Nexo is thinking... 💭*"
                                )
                        except discord.NotFound, discord.HTTPException:
                            is_expired = True

                    reply = None
                    try:
                        # Keep typing indicator active during processing
                        async with channel.typing():
                            (
                                reply,
                                used_tools,
                                usage,
                                elapsed_s,
                            ) = await process_with_mcp_tools(
                                self.bot,
                                user_id,
                                user_name,
                                prompt,
                                ctx_obj=ctx_obj,
                            )

                        # Build metadata footer
                        prompt_tok = usage.get("prompt_tokens")
                        comp_tok = usage.get("completion_tokens")
                        if prompt_tok is not None and comp_tok is not None:
                            footer_text = f"⚡ {elapsed_s:.2f}s • In: {prompt_tok:,} tokens • Out: {comp_tok:,} tokens"
                        else:
                            footer_text = f"⚡ {elapsed_s:.2f}s • Nexo KSM AIoT"

                        if reply:
                            safe_reply = reply[:4096]
                            embed = discord.Embed(
                                title="✨ Nexo Response",
                                description=safe_reply,
                                color=discord.Color.blue(),
                            )
                            embed.set_footer(text=footer_text)

                            sent_msg = None
                            if is_expired:
                                sent_msg = await channel.send(
                                    content=f"<@{user_id}> Sorry for the long wait! Here is your response:",
                                    embed=embed,
                                )
                            else:
                                try:
                                    if is_interaction:
                                        sent_msg = await ctx_obj.edit_original_response(
                                            content=None, embed=embed
                                        )
                                    else:
                                        if wait_msg:
                                            sent_msg = await wait_msg.edit(
                                                content=None, embed=embed
                                            )
                                        else:
                                            sent_msg = await channel.send(
                                                content=f"<@{user_id}>", embed=embed
                                            )
                                except discord.NotFound, discord.HTTPException:
                                    sent_msg = await channel.send(
                                        content=f"<@{user_id}>", embed=embed
                                    )

                            logger.info(
                                f"Sent response (length: {len(reply)} chars, {footer_text})"
                            )

                            # Live Footer Compaction Update (Opsi B)
                            base_footer = footer_text

                            async def on_compaction_start():
                                try:
                                    embed.set_footer(
                                        text=f"{base_footer} • 🧠 Merangkum memori..."
                                    )
                                    if is_interaction and hasattr(
                                        ctx_obj, "edit_original_response"
                                    ):
                                        await ctx_obj.edit_original_response(
                                            embed=embed
                                        )
                                    elif sent_msg and hasattr(sent_msg, "edit"):
                                        await sent_msg.edit(embed=embed)
                                except Exception as cb_err:
                                    logger.debug(
                                        f"Failed to update compaction start footer: {cb_err}"
                                    )

                            async def on_compaction_end():
                                try:
                                    embed.set_footer(
                                        text=f"{base_footer} • 🧠 Memori terpadatkan"
                                    )
                                    if is_interaction and hasattr(
                                        ctx_obj, "edit_original_response"
                                    ):
                                        await ctx_obj.edit_original_response(
                                            embed=embed
                                        )
                                    elif sent_msg and hasattr(sent_msg, "edit"):
                                        await sent_msg.edit(embed=embed)
                                except Exception as cb_err:
                                    logger.debug(
                                        f"Failed to update compaction end footer: {cb_err}"
                                    )

                            asyncio.create_task(
                                check_and_trigger_rolling_summary(
                                    user_id,
                                    ai_client,
                                    on_compaction_start=on_compaction_start,
                                    on_compaction_end=on_compaction_end,
                                )
                            )

                    except discord.NotFound, discord.HTTPException:
                        if reply:
                            await channel.send(f"<@{user_id}> {reply}")
                    except Exception as e:
                        logger.error(
                            f"Unexpected error while processing AI request for user {user_id}: {e}",
                            exc_info=True,
                        )
                        err_text = (
                            "Oops, maaf! Nexo mengalami kendala teknis saat memproses pesanmu. "
                            "Silakan coba sesaat lagi ya! 🤖"
                        )
                        if is_expired:
                            await channel.send(f"<@{user_id}> {err_text}")
                        else:
                            try:
                                if is_interaction:
                                    await ctx_obj.edit_original_response(
                                        content=err_text
                                    )
                                else:
                                    if wait_msg:
                                        await wait_msg.edit(content=err_text)
                                    else:
                                        await channel.send(f"<@{user_id}> {err_text}")
                            except discord.NotFound, discord.HTTPException:
                                await channel.send(f"<@{user_id}> {err_text}")

            except Exception as outer_err:
                logger.critical(
                    f"Fatal unhandled exception in Agent Orchestrator Worker loop: {outer_err}",
                    exc_info=True,
                )
                await asyncio.sleep(0.5)
            finally:
                self.bot.message_queue.task_done()

    @app_commands.command(name="tanya", description="Ask Nexo about KSM AIoT projects!")
    @app_commands.describe(pertanyaan="What do you want to ask?")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: (i.guild_id or 0, i.user.id))
    async def tanya(self, interaction: discord.Interaction, pertanyaan: str):
        await interaction.response.defer(ephemeral=False)

        is_healthy, error_msg = await check_services_health()
        if not is_healthy:
            await interaction.edit_original_response(content=error_msg)
            return

        if not getattr(self.bot, "cached_mcp_tools", []):
            try:
                self.bot.cached_mcp_tools = await get_tools_from_mcp_server()
            except Exception:
                await interaction.edit_original_response(
                    content="⚠️ Failed to fetch tools from the MCP Server."
                )
                return

        logger.info(f"User: {interaction.user.name} asked: {pertanyaan}")

        position = self.bot.message_queue.qsize()
        if self.bot.bot_lock.locked():
            position += 1

        if position > 0:
            await interaction.edit_original_response(
                content=f"⏳ *Your question is currently queued at position #{position}. Please wait a moment!*"
            )

        await self.bot.message_queue.put((interaction, pertanyaan, time.time()))

    @tanya.error
    async def tanya_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        if isinstance(error, app_commands.CommandOnCooldown):
            retry_in = round(error.retry_after, 1)
            msg = f"⏳ Harap tunggu **{retry_in} detik** sebelum bertanya lagi ya!"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        else:
            logger.error(f"Error in /tanya slash command: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ Terjadi kesalahan saat memproses perintah.", ephemeral=True
                )

    @app_commands.command(
        name="token-stats",
        description="View your personal token consumption and interaction stats.",
    )
    async def token_stats_slash(self, interaction: discord.Interaction):
        """Displays user's personal token usage analytics."""
        await interaction.response.defer(ephemeral=False)
        stats = await get_user_token_stats(interaction.user.id)

        embed = discord.Embed(
            title=f"📊 Token Usage Statistics — {interaction.user.display_name}",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="📥 Prompt Tokens (Input)",
            value=f"`{stats['total_prompt_tokens']:,}` tokens",
            inline=True,
        )
        embed.add_field(
            name="📤 Completion Tokens (Output)",
            value=f"`{stats['total_completion_tokens']:,}` tokens",
            inline=True,
        )
        embed.add_field(
            name="📈 Total Tokens Used",
            value=f"`{stats['total_tokens']:,}` tokens",
            inline=True,
        )
        embed.add_field(
            name="💬 Total Interactions",
            value=f"`{stats['interactions']:,}` queries",
            inline=True,
        )
        embed.add_field(
            name="⚡ Avg. Tokens / Turn",
            value=f"`{stats['avg_tokens_per_interaction']:,}` tokens",
            inline=True,
        )
        embed.set_footer(
            text="💡 Tip: Gunakan $reset untuk membersihkan konteks memori aktif."
        )
        await interaction.edit_original_response(embed=embed)

    @commands.command(name="tokenstats", aliases=["tokens", "mytoken"])
    async def token_stats_prefix(self, ctx):
        """Prefix command to view user's token statistics."""
        stats = await get_user_token_stats(ctx.author.id)

        embed = discord.Embed(
            title=f"📊 Token Usage Statistics — {ctx.author.display_name}",
            color=discord.Color.teal(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="📥 Prompt Tokens",
            value=f"`{stats['total_prompt_tokens']:,}`",
            inline=True,
        )
        embed.add_field(
            name="📤 Completion Tokens",
            value=f"`{stats['total_completion_tokens']:,}`",
            inline=True,
        )
        embed.add_field(
            name="📈 Total Tokens",
            value=f"`{stats['total_tokens']:,}`",
            inline=True,
        )
        embed.add_field(
            name="💬 Interactions",
            value=f"`{stats['interactions']:,}`",
            inline=True,
        )
        embed.add_field(
            name="⚡ Avg. Tokens/Turn",
            value=f"`{stats['avg_tokens_per_interaction']:,}`",
            inline=True,
        )
        embed.set_footer(
            text="💡 Tip: Gunakan $reset untuk membersihkan konteks memori aktif."
        )
        await ctx.reply(embed=embed)

    @app_commands.command(
        name="leaderboard-token",
        description="Top 10 token consumers in this Discord server.",
    )
    async def leaderboard_token_slash(self, interaction: discord.Interaction):
        """Displays guild token leaderboard."""
        if not interaction.guild:
            await interaction.response.send_message(
                "❌ This command can only be used in a Discord server.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=False)
        leaderboard, guild_summary = await get_guild_token_leaderboard(
            interaction.guild.id, limit=10
        )

        embed = discord.Embed(
            title=f"🏆 Token Usage Leaderboard — {interaction.guild.name}",
            description=(
                f"**Guild Total Usage:** `{guild_summary['guild_total_tokens']:,}` tokens "
                f"({guild_summary['guild_interactions']:,} interactions)\n"
                f"*(Input: `{guild_summary['guild_prompt_tokens']:,}` • Output: `{guild_summary['guild_completion_tokens']:,}`)*\n\n"
                "**Top 10 Active AI Users:**"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )

        if not leaderboard:
            description = embed.description or ""
            description += "\n*No token interactions recorded in this server yet.*"
            embed.description = description
        else:
            rank_medals = ["🥇", "🥈", "🥉"]
            for idx, item in enumerate(leaderboard, start=1):
                medal = (
                    rank_medals[idx - 1] if idx <= len(rank_medals) else f"**#{idx}**"
                )
                embed.add_field(
                    name=f"{medal} {item['username']}",
                    value=(
                        f"**Total:** `{item['total_tokens']:,}` tokens\n"
                        f"*(In: `{item['prompt_tokens']:,}` | Out: `{item['completion_tokens']:,}` | {item['interactions']} chats)*"
                    ),
                    inline=False,
                )

        embed.set_footer(text="Nexo AIoT Engine • Data diperbarui secara real-time")
        await interaction.edit_original_response(embed=embed)

    @commands.command(name="tokenleaderboard", aliases=["toptoken", "topusers"])
    async def leaderboard_token_prefix(self, ctx):
        """Prefix command for token leaderboard."""
        if not ctx.guild:
            await ctx.reply("❌ This command can only be used in a Discord server.")
            return

        leaderboard, guild_summary = await get_guild_token_leaderboard(
            ctx.guild.id, limit=10
        )

        embed = discord.Embed(
            title=f"🏆 Token Usage Leaderboard — {ctx.guild.name}",
            description=(
                f"**Guild Total Usage:** `{guild_summary['guild_total_tokens']:,}` tokens\n"
                f"*(Input: `{guild_summary['guild_prompt_tokens']:,}` • Output: `{guild_summary['guild_completion_tokens']:,}`)*\n\n"
                "**Top 10 Active AI Users:**"
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )

        if not leaderboard:
            description = embed.description or ""
            description += "\n*No token interactions recorded in this server yet.*"
            embed.description = description
        else:
            rank_medals = ["🥇", "🥈", "🥉"]
            for idx, item in enumerate(leaderboard, start=1):
                medal = (
                    rank_medals[idx - 1] if idx <= len(rank_medals) else f"**#{idx}**"
                )
                embed.add_field(
                    name=f"{medal} {item['username']}",
                    value=f"`{item['total_tokens']:,}` tokens ({item['interactions']} chats)",
                    inline=False,
                )

        embed.set_footer(text="Nexo AIoT Engine • Data diperbarui secara real-time")
        await ctx.reply(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message):
        # Prevents the bot from replying to itself or other bots
        if message.author.bot or message.author == self.bot.user:
            return

        if not self.bot.user:
            return

        # Hanya trigger jika bot di-mention secara eksplisit/langsung (bukan karena @everyone atau @here)
        is_mentioned = (
            (self.bot.user in message.mentions)
            or (f"<@{self.bot.user.id}>" in message.content)
            or (f"<@!{self.bot.user.id}>" in message.content)
        )

        if is_mentioned:
            # Check if it's a command prefix (e.g., typo) ignore
            if message.content.startswith("$"):
                return

            # Sliding Cooldown (5.0s per user)
            now = time.time()
            last_call = self._mention_cooldowns.get(message.author.id, 0.0)
            if now - last_call < 5.0:
                remaining = round(5.0 - (now - last_call), 1)
                try:
                    await message.reply(
                        f"⏳ Harap tunggu **{remaining} detik** sebelum bertanya lagi ya!",
                        delete_after=4,
                    )
                except Exception:
                    pass
                return
            self._mention_cooldowns[message.author.id] = now

            try:
                is_healthy, error_msg = await check_services_health()
                if not is_healthy:
                    await message.reply(content=error_msg)
                    return

                # Fetch previous messages history safely
                history_msgs = []
                total_chars = 0
                max_chars = int(os.environ.get("MAX_GROUP_CONTEXT_CHARS", 10000))

                try:
                    async for msg in message.channel.history(limit=10, before=message):
                        content = msg.clean_content
                        if content:
                            formatted_msg = f"{msg.author.name}: {content}"
                            if total_chars + len(formatted_msg) > max_chars:
                                break
                            history_msgs.append(formatted_msg)
                            total_chars += len(formatted_msg)
                except Exception as hist_err:
                    logger.warning(
                        f"Could not fetch channel history (permission or channel type): {hist_err}"
                    )

                history_msgs.reverse()
                context_text = "\n".join(history_msgs)

                # Clean bot mention from user message cleanly
                import re

                user_prompt = re.sub(r"<@!?\d+>", "", message.content).strip()
                if not user_prompt:
                    user_prompt = message.clean_content.replace(
                        f"@{self.bot.user.name}", ""
                    ).strip()

                if not user_prompt:
                    user_prompt = "Halo Nexo!"

                if context_text:
                    final_prompt = f"[Channel History Context]\n{context_text}\n\n[User's Message]\n{user_prompt}"
                else:
                    final_prompt = user_prompt

                logger.info(
                    f"Group Chat User: {message.author.name} tagged bot. History size: {total_chars} chars."
                )

                position = self.bot.message_queue.qsize()
                if self.bot.bot_lock.locked():
                    position += 1

                if position > 0:
                    queue_status_msg = await message.reply(
                        f"⏳ *Your question is currently queued at position #{position}. Please wait a moment!*"
                    )
                else:
                    queue_status_msg = None

                await self.bot.message_queue.put(
                    (message, final_prompt, time.time(), queue_status_msg)
                )

            except Exception as tag_err:
                logger.error(f"Error handling mentioned message: {tag_err}")


async def setup(bot):
    await bot.add_cog(AgentOrchestrator(bot))
