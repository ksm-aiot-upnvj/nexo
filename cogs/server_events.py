import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import logging
import os
import re

import discord
from discord.ext import commands, tasks
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.models import ScheduledEvent
from db.session import async_session
from utils.event_classifier import default_event_classifier
from utils.event_manager import (
    build_reminder_embed,
    detect_event_type,
    format_indonesian_date,
    format_time_wib,
    generate_dynamic_event_message,
    prune_reminder_intervals,
    to_wib,
)
from utils.schemas import (
    CheckVoiceChannelSchema,
    ClearMessagesSchema,
    DiscordEventSchema,
    DiscordPollSchema,
    DiscordThreadSchema,
    EndDiscordEventSchema,
    EndDiscordPollSchema,
    GetServerChannelsSchema,
    GetServerRolesSchema,
    ListDiscordEventsSchema,
    get_clean_schema,
)
from utils.template_renderer import render_event_template
from utils.gcal_manager import create_gcal_event, delete_gcal_event, list_gcal_events
from utils.auth_helper import has_permission

logger = logging.getLogger("server_events")


class ServerEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._reminded_cache: set[tuple[int, int]] = set()
        self._bot_created_event_ids: set[int] = set()

        # Configuration for temporary voice channels
        self.GENERATOR_CHANNEL_ID = int(
            os.getenv("GENERATOR_CHANNEL_ID", 1424293013930819694)
        )
        self.CATEGORY_ID = int(os.getenv("CATEGORY_ID", 1424293213930819694))
        self.active_temp_channels = set()
        self.has_vc_scanned = False

        # Reminder loop based on Calendar and Discord Events
        self.reminder_loop.start()
        self.sync_events_from_gcal.start()

    def cog_unload(self):
        self.reminder_loop.cancel()
        self.sync_events_from_gcal.cancel()

    async def cog_load(self):
        # Initialize properties if they don't exist
        if not hasattr(self.bot, "ai_tools"):
            self.bot.ai_tools = []
        if not hasattr(self.bot, "local_tool_handlers"):
            self.bot.local_tool_handlers = {}

        # Clear old tools from this cog if any (prevents duplication on reload)
        self.bot.ai_tools = [
            t
            for t in self.bot.ai_tools
            if t.get("function", {}).get("name")
            not in [
                "list_discord_events",
                "create_discord_event",
                "end_discord_event",
                "create_discord_thread",
                "create_discord_poll",
                "end_discord_poll",
                "get_server_channels",
                "get_server_roles",
                "clear_messages",
                "check_voice_channel",
            ]
        ]

        # 1. Register Pydantic tool schemas to bot memory using token-efficient clean schemas
        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "list_discord_events",
                    "description": "List scheduled and active events on the server.",
                    "parameters": get_clean_schema(ListDiscordEventsSchema),
                },
            }
        )
        self.bot.local_tool_handlers["list_discord_events"] = self.list_events_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "create_discord_event",
                    "description": "Schedule a new server event or meeting.",
                    "parameters": get_clean_schema(DiscordEventSchema),
                },
            }
        )
        self.bot.local_tool_handlers["create_discord_event"] = self.create_event_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "end_discord_event",
                    "description": "End, close, or stop a Discord Scheduled Event.",
                    "parameters": get_clean_schema(EndDiscordEventSchema),
                },
            }
        )
        self.bot.local_tool_handlers["end_discord_event"] = self.end_event_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "create_discord_thread",
                    "description": "Create a new thread in current channel.",
                    "parameters": get_clean_schema(DiscordThreadSchema),
                },
            }
        )
        self.bot.local_tool_handlers["create_discord_thread"] = (
            self.create_thread_handler
        )

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "create_discord_poll",
                    "description": "Create an interactive native poll.",
                    "parameters": get_clean_schema(DiscordPollSchema),
                },
            }
        )
        self.bot.local_tool_handlers["create_discord_poll"] = self.create_poll_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "end_discord_poll",
                    "description": "End an active poll and get final votes.",
                    "parameters": get_clean_schema(EndDiscordPollSchema),
                },
            }
        )
        self.bot.local_tool_handlers["end_discord_poll"] = self.end_poll_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "get_server_channels",
                    "description": "List all server text and voice channels.",
                    "parameters": get_clean_schema(GetServerChannelsSchema),
                },
            }
        )
        self.bot.local_tool_handlers["get_server_channels"] = self.get_channels_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "get_server_roles",
                    "description": "List all server roles and IDs.",
                    "parameters": get_clean_schema(GetServerRolesSchema),
                },
            }
        )
        self.bot.local_tool_handlers["get_server_roles"] = self.get_roles_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "clear_messages",
                    "description": "Delete messages in bulk.",
                    "parameters": get_clean_schema(ClearMessagesSchema),
                },
            }
        )
        self.bot.local_tool_handlers["clear_messages"] = self.clear_messages_handler

        self.bot.ai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": "check_voice_channel",
                    "description": "List users in a voice channel.",
                    "parameters": get_clean_schema(CheckVoiceChannelSchema),
                },
            }
        )
        self.bot.local_tool_handlers["check_voice_channel"] = (
            self.check_voice_channel_handler
        )

    @commands.Cog.listener()
    async def on_ready(self):
        if self.has_vc_scanned:
            return

        await self.bot.wait_until_ready()

        category = self.bot.get_channel(self.CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            logger.warning("CategoryChannel for Voice channel not found")
            self.has_vc_scanned = True
            return

        logger.info(f"Scanned and cached Category ID: {category.id}")
        cleaned_count = 0
        recovered_count = 0

        for channel in category.voice_channels:
            if channel.id == self.GENERATOR_CHANNEL_ID or not channel.name.startswith(
                "🔒 "
            ):
                continue

            if len(channel.members) == 0:
                try:
                    await channel.delete(reason="Cleanup temp channel after restart")
                    cleaned_count += 1
                except discord.HTTPException:
                    pass

            else:
                self.active_temp_channels.add(channel.id)
                recovered_count += 1

        logger.info(f"Cleaned {cleaned_count} empty temp channels")
        logger.info(f"Recovered {recovered_count} active temp channels")
        self.has_vc_scanned = True

    async def create_event_handler(self, arguments: dict, ctx_obj=None) -> str:
        """Function called by the LLM brain when create_discord_event tool is used"""
        try:
            if not has_permission(
                ctx_obj,
                "manage_events",
                allowed_roles=[
                    "Leader",
                    "Co-Leader",
                    "Staff-Core",
                    "Head – Academic & Research",
                    "Head – HRD",
                    "Head – PR & Multimedia",
                    "Admin",
                ],
            ):
                return "❌ DITOLAK: Kamu tidak memiliki izin 'Manage Events' atau peran kepengurusan yang sesuai untuk membuat acara."

            # Strict validation using Pydantic
            event_data = DiscordEventSchema.model_validate(arguments)

            # Combine date and time, then add +07:00 (WIB) to make it timezone-aware
            start_str = f"{event_data.start_date}T{event_data.start_time}+07:00"
            start_dt = datetime.fromisoformat(start_str)

            now_utc = datetime.now(timezone.utc)
            if start_dt <= now_utc:
                return "❌ Gagal membuat acara: Waktu mulai acara tidak boleh di masa lalu. Harap tentukan tanggal dan jam di masa mendatang."

            # For end_time, if provided we parse it, otherwise we set a default of +2 hours
            if event_data.end_date and event_data.end_time:
                end_str = f"{event_data.end_date}T{event_data.end_time}+07:00"
                end_dt = datetime.fromisoformat(end_str)
            else:
                end_dt = start_dt + timedelta(hours=2)

            if end_dt <= start_dt:
                return "❌ Gagal membuat acara: Waktu selesai acara harus lebih besar dari waktu mulai acara."

            # Get the guild/server object where this bot is located
            if not self.bot.guilds:
                return "❌ Failed: Bot is not currently in any server."
            guild = self.bot.guilds[0]

            # Check if the location matches a Voice Channel or Stage Channel name in the server
            vc = None
            for channel in guild.voice_channels + guild.stage_channels:
                if channel.name.lower() == event_data.location.lower():
                    vc = channel
                    break

            if vc:
                # Create internal event (in a Voice/Stage Channel)
                discord_event = await guild.create_scheduled_event(
                    name=event_data.name,
                    description=event_data.description,
                    start_time=start_dt,
                    end_time=end_dt,
                    channel=vc,
                    entity_type=discord.EntityType.voice
                    if isinstance(vc, discord.VoiceChannel)
                    else discord.EntityType.stage_instance,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
                loc_str = vc.name
            else:
                # Create external event (Somewhere Else)
                discord_event = await guild.create_scheduled_event(
                    name=event_data.name,
                    description=event_data.description,
                    start_time=start_dt,
                    end_time=end_dt,
                    entity_type=discord.EntityType.external,
                    location=event_data.location,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
                loc_str = event_data.location

            self._bot_created_event_ids.add(discord_event.id)

            gcal_data = await create_gcal_event(
                name=event_data.name,
                description=event_data.description,
                start_dt=start_dt,
                end_dt=end_dt,
                location=event_data.location,
            )
            gcal_id = gcal_data.get("id") if gcal_data else None

            event_url = f"https://discord.com/events/{guild.id}/{discord_event.id}"

            # Determine Announcement / Broadcast Channel
            announcement_channel_id = int(
                os.environ.get("ANNOUNCEMENT_CHANNEL_ID", "0")
            )
            broadcast_channel = None
            if announcement_channel_id:
                broadcast_channel = self.bot.get_channel(announcement_channel_id)
                if not broadcast_channel:
                    try:
                        broadcast_channel = await self.bot.fetch_channel(
                            announcement_channel_id
                        )
                    except Exception:
                        broadcast_channel = None

            if not broadcast_channel:
                broadcast_channel = getattr(ctx_obj, "channel", guild.system_channel)

            # Resolve Target Role / Mention
            target_role_id = None
            role_mention = None
            if getattr(event_data, "target_role", None):
                tr_clean = str(event_data.target_role).strip()
                if tr_clean.lower() in ("@everyone", "everyone"):
                    role_mention = "@everyone"
                    target_role_id = guild.id
                elif tr_clean.lower() in ("@here", "here"):
                    role_mention = "@here"
                else:
                    matched_role = None
                    role_id_match = re.search(r"\d+", tr_clean)
                    if role_id_match:
                        matched_role = guild.get_role(int(role_id_match.group(0)))
                    if not matched_role:
                        for r in guild.roles:
                            if r.name.lower() == tr_clean.lower().replace("@", ""):
                                matched_role = r
                                break
                    if matched_role:
                        target_role_id = matched_role.id
                        role_mention = matched_role.mention
                    else:
                        role_mention = tr_clean

            # Render Broadcast Template via Jinja2
            initial_context = {
                "event": {
                    "name": event_data.name,
                    "description": event_data.description or "",
                    "location": loc_str,
                    "event_url": event_url,
                },
                "formatted_date": format_indonesian_date(start_dt),
                "formatted_time": format_time_wib(start_dt),
                "role_mention": role_mention,
            }

            broadcast_msg = None
            if broadcast_channel:
                try:
                    rendered_msg = render_event_template(
                        "events/broadcast_initial.j2", initial_context
                    )
                    broadcast_msg = await broadcast_channel.send(
                        rendered_msg,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=True, roles=True, users=True
                        ),
                    )
                except Exception as b_err:
                    logger.error(f"Failed to send initial event broadcast: {b_err}")

            # Classify event using Hybrid Event Classifier
            classification = await default_event_classifier.classify_event(
                summary=event_data.name,
                description=event_data.description,
                gcal_id=gcal_id,
            )

            # Detect event type (EVENT vs DEADLINE) and prune reminder intervals based on lead time
            ev_type = detect_event_type(event_data.name)
            pruned_intervals = prune_reminder_intervals(start_dt, event_type=ev_type)

            # Persist ScheduledEvent to PostgreSQL database
            async with async_session() as session:
                db_event = ScheduledEvent(
                    id=discord_event.id,
                    gcal_event_id=gcal_id,
                    guild_id=guild.id,
                    broadcast_channel_id=broadcast_channel.id
                    if broadcast_channel
                    else guild.id,
                    broadcast_message_id=broadcast_msg.id if broadcast_msg else None,
                    name=event_data.name,
                    description=event_data.description,
                    location=loc_str,
                    start_time=start_dt,
                    end_time=end_dt,
                    event_url=event_url,
                    reminder_intervals=pruned_intervals,
                    reminders_sent=[],
                    template_name="default_reminder.j2",
                    target_role_id=target_role_id,
                    classification_label=classification.label,
                    is_discord_event=True,
                    is_active=True,
                )
                session.add(db_event)
                await session.commit()

            channel_mention = (
                f"<#{broadcast_channel.id}>"
                if broadcast_channel
                else "channel pengumuman"
            )
            return (
                f"✅ Acara '{event_data.name}' berhasil dijadwalkan dan disiarkan ke {channel_mention}!\n"
                f"🗓️ Waktu: {format_indonesian_date(start_dt)} pukul {format_time_wib(start_dt)} WIB\n"
                f"📍 Lokasi: {loc_str}\n"
                f"🔗 Link Event: {event_url}"
            )

        except Exception as e:
            # Error can come from Pydantic (wrong format) or Discord API (400 Bad Request, past time, etc.)
            return f"❌ Failed to create event. Error: {str(e)} (If this is a 'Cannot schedule event in the past' error, it means you set the wrong time/date, make sure to set a future time)."

    async def list_events_handler(self, arguments: dict, ctx_obj=None) -> str:
        """Handler to list Discord Scheduled Events on the server (token-efficient)."""
        try:
            req_data = ListDiscordEventsSchema.model_validate(arguments or {})
            if not self.bot.guilds:
                return "❌ Failed: Bot is not currently in any server."
            guild = (
                getattr(ctx_obj, "guild", self.bot.guilds[0])
                if ctx_obj
                else self.bot.guilds[0]
            )

            try:
                events = await guild.fetch_scheduled_events()
            except Exception as fetch_err:
                logger.warning(
                    f"Could not fetch scheduled events via API, fallback to cache: {fetch_err}"
                )
                events = list(guild.scheduled_events)

            filter_mode = (req_data.status_filter or "all").lower().strip()
            if filter_mode == "active":
                events = [e for e in events if e.status == discord.EventStatus.active]
            elif filter_mode == "scheduled":
                events = [
                    e for e in events if e.status == discord.EventStatus.scheduled
                ]
            elif filter_mode == "completed":
                events = [
                    e for e in events if e.status == discord.EventStatus.completed
                ]
            elif filter_mode in ("canceled", "cancelled"):
                events = [e for e in events if e.status == discord.EventStatus.canceled]

            if not events:
                return "Tidak ada acara terjadwal di server saat ini."

            lines = []
            for e in events:
                if e.status == discord.EventStatus.active:
                    status_str = "ACTIVE"
                elif e.status == discord.EventStatus.scheduled:
                    status_str = "SCHEDULED"
                elif e.status == discord.EventStatus.completed:
                    status_str = "COMPLETED"
                elif e.status == discord.EventStatus.canceled:
                    status_str = "CANCELED"
                else:
                    status_str = str(e.status).upper()

                dt_wib = to_wib(e.start_time)
                time_str = dt_wib.strftime("%d %b %H:%M")
                lines.append(f"- [{status_str}] {e.name} (ID: {e.id}) @ {time_str} WIB")

            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error in list_events_handler: {e}")
            return f"❌ Gagal mengambil daftar acara: {str(e)}"

    async def end_event_handler(self, arguments: dict, ctx_obj=None) -> str:
        """Handler to end/stop a Discord Scheduled Event via tool calling with robust target resolution"""
        try:
            if not has_permission(
                ctx_obj,
                "manage_events",
                allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin"],
            ):
                return "❌ DITOLAK: Kamu tidak memiliki izin 'Manage Events' atau peran kepengurusan yang sesuai untuk mengakhiri acara."

            req_data = EndDiscordEventSchema.model_validate(arguments or {})
            if not self.bot.guilds:
                return "❌ Failed: Bot is not currently in any server."
            guild = (
                getattr(ctx_obj, "guild", self.bot.guilds[0])
                if ctx_obj
                else self.bot.guilds[0]
            )

            # Target Resolution: Priority 1 (event_id), Priority 2 (event_name), Priority 3 (Active / Latest)
            target_event = None
            if req_data.event_id:
                target_event = guild.get_scheduled_event(req_data.event_id)
                if not target_event:
                    try:
                        target_event = await guild.fetch_scheduled_event(
                            req_data.event_id
                        )
                    except Exception:
                        target_event = None
                if not target_event:
                    return f"❌ Tidak ditemukan acara dengan ID {req_data.event_id} di server."

            elif req_data.event_name:
                try:
                    events = await guild.fetch_scheduled_events()
                except Exception:
                    events = list(guild.scheduled_events)

                target_name_lower = req_data.event_name.lower().strip()
                matched = [e for e in events if target_name_lower in e.name.lower()]
                if matched:
                    active_matched = [
                        e for e in matched if e.status == discord.EventStatus.active
                    ]
                    target_event = active_matched[0] if active_matched else matched[0]
                else:
                    return f"❌ Tidak ditemukan acara dengan nama/keyword '{req_data.event_name}' di server."

            else:
                try:
                    events = await guild.fetch_scheduled_events()
                except Exception:
                    events = list(guild.scheduled_events)

                active_events = [
                    e for e in events if e.status == discord.EventStatus.active
                ]
                if active_events:
                    target_event = active_events[0]
                elif events:
                    target_event = events[0]
                else:
                    return "❌ Tidak ada acara aktif atau terjadwal di server untuk diakhiri."

            if not target_event:
                return "❌ Tidak ditemukan acara aktif atau terjadwal di server untuk diakhiri."

            # Discord Lifecycle State Machine
            try:
                if target_event.status == discord.EventStatus.active:
                    try:
                        await target_event.end()
                    except Exception as end_err:
                        logger.warning(
                            f"target_event.end() failed: {end_err}, falling back to delete()"
                        )
                        await target_event.delete()
                elif target_event.status == discord.EventStatus.scheduled:
                    try:
                        if hasattr(target_event, "cancel"):
                            await target_event.cancel()
                        else:
                            await target_event.delete()
                    except Exception as cancel_err:
                        logger.warning(
                            f"target_event.cancel() failed: {cancel_err}, falling back to delete()"
                        )
                        await target_event.delete()
                else:
                    await target_event.delete()
            except Exception as discord_err:
                logger.error(
                    f"Failed to end/delete discord event via API: {discord_err}"
                )

            # Update DB state
            async with async_session() as session:
                result = await session.execute(
                    select(ScheduledEvent).where(ScheduledEvent.id == target_event.id)
                )
                db_event = result.scalar_one_or_none()
                if db_event and db_event.gcal_event_id:
                    await delete_gcal_event(db_event.gcal_event_id)
                if db_event:
                    db_event.is_active = False
                    await session.commit()

            # Send broadcast completed if channel available
            announcement_channel_id = int(
                os.environ.get("ANNOUNCEMENT_CHANNEL_ID", "0")
            )
            broadcast_channel = None
            if announcement_channel_id:
                broadcast_channel = self.bot.get_channel(announcement_channel_id)
            if not broadcast_channel:
                broadcast_channel = getattr(ctx_obj, "channel", guild.system_channel)

            if broadcast_channel:
                try:
                    loc_name = target_event.location or (
                        target_event.channel.name if target_event.channel else ""
                    )
                    fallback_text = render_event_template(
                        "events/broadcast_completed.j2",
                        {
                            "event": {
                                "name": target_event.name,
                                "location": loc_name,
                                "event_url": f"https://discord.com/events/{guild.id}/{target_event.id}",
                            },
                            "role_mention": None,
                        },
                    )
                    comp_text = await generate_dynamic_event_message(
                        event_type="completed",
                        event_name=target_event.name,
                        event_description=target_event.description,
                        fallback_text=fallback_text,
                        role_mention=None,
                        timeout_sec=120.0,
                    )
                    await broadcast_channel.send(
                        comp_text,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=True, roles=True, users=True
                        ),
                    )
                except Exception as c_err:
                    logger.error(f"Failed to send broadcast completed message: {c_err}")

            return f"✅ Acara '{target_event.name}' berhasil diakhiri dan ditutup dari daftar server."

        except Exception as e:
            return f"❌ Gagal mengakhiri acara. Error: {str(e)}"

    async def create_thread_handler(self, arguments: dict, ctx_obj=None):
        try:
            if not ctx_obj:
                return "❌ Failed to create thread: ctx_obj not found (bot doesn't know which channel to create the thread in)."

            if not has_permission(
                ctx_obj,
                "create_public_threads",
                allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin", "Member"],
            ):
                return "❌ DITOLAK: Kamu tidak memiliki izin untuk membuat thread di server ini."

            thread_data = DiscordThreadSchema.model_validate(arguments)
            channel = ctx_obj.channel

            # Can only create threads in a Text Channel
            if not isinstance(channel, discord.TextChannel):
                return "❌ Failed to create thread: This command can only be used in regular Text Channels."

            # Create thread, auto archive 10080 minutes (1 week)
            thread = await channel.create_thread(
                name=thread_data.name,
                type=discord.ChannelType.public_thread,
                auto_archive_duration=10080,
                reason=thread_data.reason,
            )

            return f"✅ Successfully created a thread named '{thread.name}'. Invite the user to continue the chat in that thread: <#{thread.id}>."

        except Exception as e:
            return f"❌ Failed to create thread. Error: {str(e)}"

    async def create_poll_handler(self, arguments: dict, ctx_obj=None):
        try:
            if not ctx_obj:
                return "❌ Failed to create poll: ctx_obj not found."

            if not has_permission(
                ctx_obj,
                "send_messages",
                allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin", "Member"],
            ):
                return "❌ DITOLAK: Kamu tidak memiliki izin untuk membuat poll di channel ini."

            poll_data = DiscordPollSchema.model_validate(arguments)
            channel = ctx_obj.channel

            if len(poll_data.options) > 10:
                return "❌ Failed: Maximum poll options is 10."

            duration = timedelta(hours=min(poll_data.duration_hours, 168))
            poll = discord.Poll(
                question=poll_data.question,
                duration=duration,
                multiple=poll_data.allow_multiselect,
            )
            for opt in poll_data.options:
                poll.add_answer(text=opt)

            await channel.send(poll=poll)
            return f"✅ Successfully created a poll with the question: '{poll_data.question}'"
        except Exception as e:
            return f"❌ Failed to create poll. Error: {str(e)}"

    async def end_poll_handler(self, arguments: dict, ctx_obj=None):
        try:
            if not ctx_obj:
                return "❌ Failed to end poll: ctx_obj not found."

            if not has_permission(
                ctx_obj,
                "manage_messages",
                allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin"],
            ):
                return "❌ DITOLAK: Kamu tidak memiliki izin 'Manage Messages' atau peran kepengurusan untuk mengakhiri poll."

            schema = EndDiscordPollSchema.model_validate(arguments)
            channel = ctx_obj.channel

            target_msg = None
            if schema.message_id:
                try:
                    target_msg = await channel.fetch_message(schema.message_id)
                except discord.NotFound:
                    return f"❌ Message with ID {schema.message_id} was not found in this channel."
            else:
                # Search recent messages for an active poll
                async for msg in channel.history(limit=50):
                    if msg.poll and not msg.poll.is_finalised():
                        target_msg = msg
                        break

            if not target_msg or not target_msg.poll:
                return "❌ No active poll found in this channel to end. If you have the specific message ID, please specify it."

            if target_msg.poll.is_finalised():
                return f"ℹ️ The poll '{target_msg.poll.question}' has already been closed/finalized."

            # End the poll early
            ended_msg = await target_msg.end_poll()
            poll = ended_msg.poll or target_msg.poll

            result_lines = []
            for ans in poll.answers:
                result_lines.append(f"• **{ans.text}**: {ans.vote_count} votes")

            results_str = "\n".join(result_lines)
            return (
                f"✅ Successfully ended the poll: **'{poll.question}'**\n\n"
                f"📊 **Final Results:**\n{results_str}"
            )
        except discord.Forbidden:
            return "❌ I don't have permission to end this poll (requires 'Manage Messages' permission or bot must be the creator)."
        except Exception as e:
            return f"❌ Failed to end poll. Error: {str(e)}"

    async def check_voice_channel_handler(self, arguments: dict, ctx_obj=None) -> str:
        try:
            schema = CheckVoiceChannelSchema.model_validate(arguments)
            if not ctx_obj or not ctx_obj.guild:
                return "❌ Cannot check voice channel because the server (guild) context was not found."

            target_vc = None

            # 1. Search by channel_name if specified
            if schema.channel_name and schema.channel_name.strip():
                c_name = schema.channel_name.lower().strip()
                for vc in ctx_obj.guild.voice_channels:
                    if c_name in vc.name.lower():
                        target_vc = vc
                        break

            # 2. Fallback to current channel if it is a Voice/Stage Channel (Voice Chat text channel)
            if not target_vc and hasattr(ctx_obj, "channel"):
                if isinstance(
                    ctx_obj.channel, (discord.VoiceChannel, discord.StageChannel)
                ):
                    target_vc = ctx_obj.channel

            # 3. Fallback to the user's currently connected Voice Channel
            if not target_vc:
                user_obj = getattr(ctx_obj, "author", getattr(ctx_obj, "user", None))
                if (
                    user_obj
                    and hasattr(user_obj, "voice")
                    and user_obj.voice
                    and user_obj.voice.channel
                ):
                    target_vc = user_obj.voice.channel

            if not target_vc:
                if schema.channel_name:
                    return f"❌ Voice Channel containing the word '{schema.channel_name}' was not found."
                return "❌ No voice channel specified, and you are not currently in a voice channel nor typing inside a voice channel text chat."

            members = target_vc.members
            if not members:
                return f"ℹ️ Currently, there is no one in the Voice Channel '{target_vc.name}'."

            member_list = [f"- {m.display_name} ({m.name})" for m in members]
            return (
                f"✅ List of people currently in the Voice Channel '{target_vc.name}' ({len(members)} people):\n"
                + "\n".join(member_list)
            )

        except Exception as e:
            logger.error(f"Error in check_voice_channel_handler: {e}")
            return f"❌ Failed to check voice channel: {str(e)}"

    async def get_channels_handler(self, arguments: dict, ctx_obj=None):
        try:
            if not self.bot.guilds:
                return "❌ Error: Bot is not in any server."
            guild = self.bot.guilds[0]

            channels_info = []
            for c in guild.channels:
                if isinstance(c, discord.CategoryChannel):
                    continue
                channels_info.append(
                    {"id": str(c.id), "name": c.name, "type": str(c.type)}
                )
            return json.dumps(channels_info, indent=2)
        except Exception as e:
            return f"❌ Failed to get channel list: {str(e)}"

    async def get_roles_handler(self, arguments: dict, ctx_obj=None):
        try:
            if not self.bot.guilds:
                return "❌ Error: Bot is not in any server."
            guild = self.bot.guilds[0]

            roles_info = [
                {"id": str(r.id), "name": r.name}
                for r in guild.roles
                if r.name != "@everyone"
            ]
            return json.dumps(roles_info, indent=2)
        except Exception as e:
            return f"❌ Failed to get role list: {str(e)}"

    async def clear_messages_handler(self, arguments: dict, ctx_obj=None):
        try:
            if not ctx_obj:
                return "❌ Failed: ctx_obj not found."

            if not has_permission(
                ctx_obj,
                "manage_messages",
                allowed_roles=["Leader", "Co-Leader", "Staff-Core", "Admin"],
            ):
                return "❌ DITOLAK: Kamu tidak memiliki izin 'Manage Messages' di server ini."

            req_data = ClearMessagesSchema.model_validate(arguments)
            limit = min(req_data.limit, 500)

            asyncio.create_task(
                self._background_delete(ctx_obj.channel, limit, req_data.only_today)
            )
            return f"✅ Processing message deletion (Limit: {limit}, Only Today: {req_data.only_today}) in the background. Tell the user to wait a moment."

        except Exception as e:
            return f"❌ Failed to process clear_messages: {str(e)}"

    async def _background_delete(self, channel, limit: int, only_today: bool):
        try:
            # Use discord.utils.utcnow() to avoid timezone offset-naive errors
            today_start = discord.utils.utcnow().replace(
                hour=0, minute=0, second=0, microsecond=0
            )

            def check_msg(msg):
                if only_today:
                    return msg.created_at >= today_start
                return True

            # channel.purge() automatically handles bulk_delete (<14 days) and single delete (>14 days)
            deleted = await channel.purge(limit=limit + 2, check=check_msg)
            logger.info(
                f"Successfully deleted {len(deleted)} messages in channel {channel.name}."
            )
        except discord.Forbidden:
            logger.error(
                "Failed to delete messages: Bot does not have Manage Messages permission."
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to delete messages (HTTP Exception): {e}")
        except Exception as e:
            logger.error(f"Unexpected error during message purge: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Send a welcome message when a new member joins"""
        channel_id = int(os.environ.get("WELCOME_AND_RULES_CHANNEL_ID", 0))

        try:
            channel = self.bot.get_channel(channel_id)
            if channel:
                welcome_msg = (
                    f"Welcome {member.mention} to the KSM AIoT server! 👋\n"
                    f"To get full access, please introduce yourself using the following format:\n\n"
                    f"```text\n"
                    f"Name:\n"
                    f"Nickname:\n"
                    f"NIM:\n"
                    f"Major(IF/SI/D3SI/DS): \n"
                    f"Batch/Year:\n"
                    f"Hobby:\n"
                    f"Interest:\n"
                    f"```\n"
                    f"💡 *Note: Discord automatically removes accounts without roles when disconnected/closed. Please complete your intro to keep your access!*"
                )
                await channel.send(welcome_msg)
        except discord.Forbidden:
            logger.error(
                "Failed to send welcome message: Bot does not have Send Messages or View Channel permission."
            )
        except discord.HTTPException as e:
            logger.error(f"Failed to send welcome message (HTTP Exception): {e}")
        except Exception as e:
            logger.error(f"Unexpected error during welcome message: {e}")

    @commands.Cog.listener()
    async def on_message(self, message):
        """Check messages in a specific channel to automatically assign roles"""
        # Ignore messages from the bot itself
        if message.author.bot:
            return

        try:
            channel_id = int(os.environ.get("WELCOME_AND_RULES_CHANNEL_ID", 0))
        except ValueError:
            logger.error("Invalid channel ID in environment variable.")
            return

        # Only monitor messages in the welcome-and-rules channel
        if message.channel.id == channel_id:
            fields = {}
            for line in message.content.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                fields[key.strip().lower()] = value.strip()

            introduction_fields = {
                "name",
                "nickname",
                "nim",
                "major(if/si/d3si/ds)",
                "batch/year",
                "hobby",
                "interest",
            }
            if not introduction_fields.intersection(fields):
                return

            required_fields = [
                "name",
                "nickname",
                "nim",
                "major(if/si/d3si/ds)",
                "batch/year",
                "hobby",
                "interest",
            ]
            missing_fields = [
                field for field in required_fields if not fields.get(field)
            ]
            if missing_fields:
                await message.reply(
                    "⚠️ Your introduction is incomplete. Missing: "
                    + ", ".join(missing_fields)
                    + ".",
                    delete_after=10,
                )
                return

            nim = fields["nim"]
            if not re.fullmatch(r"\d{8,}", nim):
                await message.reply(
                    "⚠️ NIM must contain at least 8 digits.", delete_after=10
                )
                return

            member_role_id = int(os.environ.get("MEMBER_ROLE_ID", 0))
            role = message.guild.get_role(member_role_id) if member_role_id else None

            if not role:
                await message.reply(
                    "⚠️ I could not find the member role. Please tell the admin to create it.",
                    delete_after=10,
                )
                return

            try:
                if role not in message.author.roles:
                    await message.author.add_roles(role)

                new_nickname = (
                    f"{fields['nickname']} {fields['major(if/si/d3si/ds)'].upper()}"
                    f"'{nim[:2]} {nim[7:]}"
                )[:32]
                if message.author.nick != new_nickname:
                    await message.author.edit(nick=new_nickname)

                await message.add_reaction("👋")
                await message.reply(
                    f"Nice to meet you, {fields['nickname']}! Welcome to KSM AIoT! 🚀",
                )
            except discord.Forbidden:
                await message.reply(
                    "⚠️ The KSM AIoT Bot does not have permission to assign the role or change your nickname.",
                    delete_after=10,
                )
            except Exception as e:
                logger.error(f"Error processing introduction: {e}")

    @tasks.loop(seconds=60)
    async def reminder_loop(self):
        """Background task checking scheduled event reminders, auto-start, and auto-end every 60s"""
        try:
            now_utc = datetime.now(timezone.utc)
            async with async_session() as session:
                result = await session.execute(
                    select(ScheduledEvent).where(
                        ScheduledEvent.is_active == True,  # noqa: E712
                    )
                )
                active_events = result.scalars().all()

                for ev in active_events:
                    expiry_time = ev.end_time or (ev.start_time + timedelta(hours=2))

                    # Get Discord guild and ScheduledEvent object
                    guild = self.bot.get_guild(ev.guild_id) or (
                        self.bot.guilds[0] if self.bot.guilds else None
                    )
                    discord_event = None
                    if guild:
                        discord_event = guild.get_scheduled_event(ev.id)
                        if not discord_event:
                            try:
                                discord_event = await guild.fetch_scheduled_event(ev.id)
                            except Exception:
                                discord_event = None

                    # 1. AUTO-END: Check if event has ended
                    if now_utc >= expiry_time:
                        if discord_event and discord_event.status in (
                            discord.EventStatus.active,
                            discord.EventStatus.scheduled,
                        ):
                            try:
                                await discord_event.end()
                                logger.info(
                                    f"Auto-ended scheduled event {ev.id} ({ev.name})"
                                )
                            except Exception as end_err:
                                logger.warning(
                                    f"Could not auto-end discord event {ev.id}: {end_err}"
                                )

                            # Broadcast completion message
                            channel = self.bot.get_channel(ev.broadcast_channel_id)
                            if channel:
                                role_mention_str = (
                                    f"<@&{ev.target_role_id}>"
                                    if ev.target_role_id
                                    else None
                                )
                                comp_ctx = {
                                    "event": {
                                        "name": ev.name,
                                        "location": ev.location,
                                        "event_url": ev.event_url,
                                    },
                                    "role_mention": role_mention_str,
                                }
                                try:
                                    fallback_text = render_event_template(
                                        "events/broadcast_completed.j2", comp_ctx
                                    )
                                    comp_text = await generate_dynamic_event_message(
                                        event_type="completed",
                                        event_name=ev.name,
                                        event_description=ev.description,
                                        fallback_text=fallback_text,
                                        role_mention=role_mention_str,
                                        timeout_sec=120.0,
                                    )
                                    await channel.send(
                                        comp_text,
                                        allowed_mentions=discord.AllowedMentions(
                                            everyone=True, roles=True, users=True
                                        ),
                                    )
                                except Exception as comp_err:
                                    logger.error(
                                        f"Failed to send completion broadcast: {comp_err}"
                                    )

                        ev.is_active = False
                        await session.commit()
                        continue

                    # 2. AUTO-START & BROADCAST STARTED: Check if event start time has arrived
                    sent = list(ev.reminders_sent or [])
                    if (
                        now_utc >= ev.start_time
                        and 0 not in sent
                        and (ev.id, 0) not in self._reminded_cache
                    ):
                        # Claim/Lock in memory and DB immediately to prevent concurrent broadcast
                        self._reminded_cache.add((ev.id, 0))
                        sent.append(0)
                        ev.reminders_sent = sent
                        flag_modified(ev, "reminders_sent")
                        await session.commit()

                        # If voice/stage channel event is still scheduled, try to start it
                        if (
                            discord_event
                            and discord_event.status == discord.EventStatus.scheduled
                            and discord_event.channel
                        ):
                            try:
                                await discord_event.start()
                                logger.info(
                                    f"Auto-started scheduled event {ev.id} ({ev.name})"
                                )
                            except Exception as start_api_err:
                                logger.warning(
                                    f"Could not auto-start discord event {ev.id}: {start_api_err}"
                                )

                        # Broadcast started message to announcement channel
                        channel = self.bot.get_channel(ev.broadcast_channel_id)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(
                                    ev.broadcast_channel_id
                                )
                            except Exception:
                                channel = None

                        if channel:
                            role_mention_str = (
                                f"<@&{ev.target_role_id}>"
                                if ev.target_role_id
                                else None
                            )
                            start_ctx = {
                                "event": {
                                    "name": ev.name,
                                    "location": ev.location,
                                    "event_url": ev.event_url,
                                },
                                "role_mention": role_mention_str,
                            }
                            try:
                                fallback_start = render_event_template(
                                    "events/broadcast_started.j2",
                                    start_ctx,
                                )
                                start_text = await generate_dynamic_event_message(
                                    event_type="started",
                                    event_name=ev.name,
                                    event_description=ev.description,
                                    fallback_text=fallback_start,
                                    role_mention=role_mention_str,
                                    timeout_sec=120.0,
                                )
                                await channel.send(
                                    start_text,
                                    allowed_mentions=discord.AllowedMentions(
                                        everyone=True, roles=True, users=True
                                    ),
                                )
                                logger.info(
                                    f"Berhasil mengirim broadcast started untuk event '{ev.name}'"
                                )
                            except Exception as start_err:
                                logger.error(
                                    f"Failed to send started broadcast for event {ev.id}: {start_err}"
                                )

                    # 3. MULTI-INTERVAL REMINDERS: Check due reminder intervals
                    ev_type = detect_event_type(ev.name)
                    intervals = ev.reminder_intervals or []
                    dirty = False

                    for interval in intervals:
                        if (
                            interval in sent
                            or (ev.id, interval) in self._reminded_cache
                        ):
                            continue

                        trigger_time = ev.start_time - timedelta(minutes=interval)
                        # Tolerance window buffer: trigger_time <= now_utc < ev.start_time
                        if now_utc >= trigger_time and now_utc < ev.start_time:
                            channel = self.bot.get_channel(ev.broadcast_channel_id)
                            if not channel:
                                try:
                                    channel = await self.bot.fetch_channel(
                                        ev.broadcast_channel_id
                                    )
                                except Exception:
                                    channel = None

                            if channel:
                                embed = build_reminder_embed(
                                    event_name=ev.name,
                                    description=ev.description,
                                    start_dt=ev.start_time,
                                    location=ev.location,
                                    interval_minutes=interval,
                                    event_type=ev_type,
                                    event_url=ev.event_url,
                                )
                                role_mention_str = (
                                    f"<@&{ev.target_role_id}>"
                                    if ev.target_role_id
                                    else None
                                )
                                try:
                                    await channel.send(
                                        content=role_mention_str,
                                        embed=embed,
                                        allowed_mentions=discord.AllowedMentions(
                                            everyone=True, roles=True, users=True
                                        ),
                                    )
                                except Exception as send_err:
                                    logger.error(
                                        f"Failed to send reminder for event {ev.id}: {send_err}"
                                    )

                            sent.append(interval)
                            self._reminded_cache.add((ev.id, interval))
                            dirty = True

                    if dirty:
                        ev.reminders_sent = sent
                        flag_modified(ev, "reminders_sent")
                        await session.commit()

                await session.commit()
        except Exception as e:
            logger.error(f"Error in reminder_loop: {e}")

    @tasks.loop(minutes=15)
    async def sync_events_from_gcal(self):
        """Mendeteksi agenda baru dari Google Calendar dan membuatnya di Discord."""
        if not self.bot.guilds:
            return

        now_utc = datetime.now(timezone.utc)
        try:
            gcal_items = await list_gcal_events(time_min=now_utc)
        except Exception as gcal_err:
            logger.error(f"Error listing gcal events in sync loop: {gcal_err}")
            return

        for guild in self.bot.guilds:
            announcement_channel_id = int(
                os.environ.get("ANNOUNCEMENT_CHANNEL_ID", "0")
            )
            broadcast_channel = (
                self.bot.get_channel(announcement_channel_id)
                if announcement_channel_id
                else guild.system_channel
            )

            async with async_session() as session:
                for item in gcal_items:
                    gcal_id = item.get("id")
                    if not gcal_id:
                        continue

                    # Cek apakah sudah pernah tersimpan di DB
                    res = await session.execute(
                        select(ScheduledEvent).where(
                            ScheduledEvent.gcal_event_id == gcal_id
                        )
                    )
                    if res.scalar_one_or_none():
                        continue  # Sudah tersinkron

                    start_obj = item.get("start", {})
                    end_obj = item.get("end", {})
                    is_all_day = "date" in start_obj and "dateTime" not in start_obj

                    start_iso = start_obj.get("dateTime") or start_obj.get("date")
                    end_iso = end_obj.get("dateTime") or end_obj.get("date")
                    if not start_iso:
                        continue

                    start_dt = datetime.fromisoformat(start_iso)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)

                    end_dt = (
                        datetime.fromisoformat(end_iso)
                        if end_iso
                        else start_dt + timedelta(hours=2)
                    )
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)

                    name = item.get("summary", "Kegiatan KSM AIoT")
                    desc = item.get(
                        "description",
                        "Agenda resmi dari Google Calendar KSM AIoT.",
                    )
                    loc = item.get("location", "Discord Voice Channel / Lab IoT")
                    ev_type = detect_event_type(name, is_all_day=is_all_day)

                    # Cek validitas waktu terhadap masa sekarang (Anti-Past Event Error)
                    current_now_utc = datetime.now(timezone.utc)
                    if end_dt <= current_now_utc:
                        # 1. Event sudah selesai sepenuhnya di masa lalu -> Skip dengan tenang
                        logger.debug(
                            f"Mengabaikan event lampau '{name}' dari Google Calendar (selesai pada {end_dt})"
                        )
                        continue

                    # 2. Jika event sedang berlangsung (start_dt <= now < end_dt), sesuaikan waktu mulai untuk Discord
                    discord_start_time = start_dt
                    if start_dt <= current_now_utc:
                        discord_start_time = current_now_utc + timedelta(minutes=1)
                        if discord_start_time >= end_dt:
                            end_dt = discord_start_time + timedelta(hours=1)

                    # 3. Classify event using Hybrid Event Classifier
                    classification = await default_event_classifier.classify_event(
                        summary=name, description=desc, gcal_id=gcal_id
                    )

                    if not classification.is_discord_event:
                        logger.info(
                            f"Agenda GCal '{name}' diklasifikasikan sebagai '{classification.label}' (is_discord_event=False). Melewati pembuatan Discord Scheduled Event."
                        )
                        hash_id = int(
                            hashlib.sha256(gcal_id.encode()).hexdigest()[:14],
                            16,
                        )
                        db_record = ScheduledEvent(
                            id=hash_id,
                            gcal_event_id=gcal_id,
                            guild_id=guild.id,
                            broadcast_channel_id=broadcast_channel.id
                            if broadcast_channel
                            else guild.id,
                            name=name,
                            description=desc,
                            location=loc,
                            start_time=start_dt,
                            end_time=end_dt,
                            event_url="",
                            classification_label=classification.label,
                            is_discord_event=False,
                            is_active=False,
                            reminder_intervals=[],
                            reminders_sent=[],
                        )
                        session.add(db_record)
                        await session.commit()
                        continue

                    # Cek apakah event dengan nama & waktu mirip sudah ada di Discord (cegah duplikasi)
                    existing_discord_event = None
                    for ev in guild.scheduled_events:
                        if (
                            ev.name.strip().lower() == name.strip().lower()
                            and abs(
                                (ev.start_time - discord_start_time).total_seconds()
                            )
                            < 600
                        ):
                            existing_discord_event = ev
                            break

                    if existing_discord_event:
                        logger.info(
                            f"Event '{name}' dari Google Calendar sudah ada di Discord (ID {existing_discord_event.id}). Menghubungkan ke database tanpa membuat duplikat."
                        )
                        self._bot_created_event_ids.add(existing_discord_event.id)
                        res_exist = await session.execute(
                            select(ScheduledEvent).where(
                                ScheduledEvent.id == existing_discord_event.id
                            )
                        )
                        db_rec = res_exist.scalar_one_or_none()
                        if db_rec:
                            if not db_rec.gcal_event_id:
                                db_rec.gcal_event_id = gcal_id
                                await session.commit()
                        else:
                            event_url = f"https://discord.com/events/{guild.id}/{existing_discord_event.id}"
                            pruned_intervals = prune_reminder_intervals(
                                discord_start_time, now_utc, event_type=ev_type
                            )
                            db_record = ScheduledEvent(
                                id=existing_discord_event.id,
                                gcal_event_id=gcal_id,
                                guild_id=guild.id,
                                broadcast_channel_id=broadcast_channel.id
                                if broadcast_channel
                                else guild.id,
                                broadcast_message_id=None,
                                name=name,
                                description=desc,
                                location=loc,
                                start_time=start_dt,
                                end_time=end_dt,
                                event_url=event_url,
                                classification_label=classification.label,
                                is_discord_event=True,
                                is_active=True,
                                reminder_intervals=pruned_intervals,
                                reminders_sent=[],
                            )
                            session.add(db_record)
                            await session.commit()
                        continue

                    # Buat Scheduled Event baru di Discord
                    try:
                        discord_event = await guild.create_scheduled_event(
                            name=name[:100],
                            description=desc[:1000],
                            start_time=discord_start_time,
                            end_time=end_dt,
                            entity_type=discord.EntityType.external,
                            location=loc[:100],
                            privacy_level=discord.PrivacyLevel.guild_only,
                        )
                        self._bot_created_event_ids.add(discord_event.id)

                        event_url = (
                            f"https://discord.com/events/{guild.id}/{discord_event.id}"
                        )
                        pruned_intervals = prune_reminder_intervals(
                            discord_start_time, now_utc, event_type=ev_type
                        )

                        # Simpan ke DB SEGERA untuk mencegah race-condition dengan listener
                        db_record = ScheduledEvent(
                            id=discord_event.id,
                            gcal_event_id=gcal_id,
                            guild_id=guild.id,
                            broadcast_channel_id=broadcast_channel.id
                            if broadcast_channel
                            else guild.id,
                            broadcast_message_id=None,
                            name=name,
                            description=desc,
                            location=loc,
                            start_time=start_dt,
                            end_time=end_dt,
                            event_url=event_url,
                            classification_label=classification.label,
                            is_discord_event=True,
                            is_active=True,
                            reminder_intervals=pruned_intervals,
                            reminders_sent=[],
                        )
                        session.add(db_record)
                        await session.commit()

                        # Render Broadcast Template via Jinja2
                        broadcast_msg = None
                        if broadcast_channel:
                            try:
                                initial_context = {
                                    "event": {
                                        "name": name,
                                        "description": desc or "",
                                        "location": loc,
                                        "event_url": event_url,
                                    },
                                    "formatted_date": format_indonesian_date(start_dt),
                                    "formatted_time": format_time_wib(start_dt),
                                    "role_mention": None,
                                }
                                rendered_msg = render_event_template(
                                    "events/broadcast_initial.j2", initial_context
                                )
                                broadcast_msg = await broadcast_channel.send(
                                    rendered_msg,
                                    allowed_mentions=discord.AllowedMentions(
                                        everyone=True, roles=True, users=True
                                    ),
                                )
                                if broadcast_msg:
                                    db_record.broadcast_message_id = broadcast_msg.id
                                    await session.commit()
                            except Exception as b_err:
                                logger.error(
                                    f"Failed to send initial event broadcast for GCal event: {b_err}"
                                )

                        logger.info(
                            f"Berhasil mensinkronkan event '{name}' ({ev_type}/{classification.label}) dari Google Calendar ke Discord!"
                        )
                    except Exception as e:
                        logger.error(f"Gagal membuat Discord Event dari GCal: {e}")

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        try:
            if hasattr(self.bot, "wait_until_ready"):
                res = self.bot.wait_until_ready()
                if inspect.isawaitable(res):
                    await res
        except RuntimeError, asyncio.CancelledError:
            pass

    @sync_events_from_gcal.before_loop
    async def before_sync_events_from_gcal(self):
        try:
            if hasattr(self.bot, "wait_until_ready"):
                res = self.bot.wait_until_ready()
                if inspect.isawaitable(res):
                    await res
        except RuntimeError, asyncio.CancelledError:
            pass

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent):
        """
        Gateway listener triggered when an event is created in Discord (e.g. via UI).
        Syncs event to Google Calendar, saves to PostgreSQL, and broadcasts initial announcement.
        """
        try:
            # 1. Abaikan event yang dibuat oleh Nexo sendiri (mencegah loop/duplikasi)
            if event.id in self._bot_created_event_ids:
                logger.debug(
                    f"Mengabaikan on_scheduled_event_create untuk bot-created event {event.id}"
                )
                return

            if self.bot.user and (
                event.creator_id == self.bot.user.id
                or (event.creator and event.creator.id == self.bot.user.id)
            ):
                logger.debug(
                    f"Mengabaikan on_scheduled_event_create: event {event.id} dibuat oleh bot user"
                )
                return

            # Check if this event is already registered in DB (e.g. created by bot tool)
            async with async_session() as session:
                res = await session.execute(
                    select(ScheduledEvent).where(ScheduledEvent.id == event.id)
                )
                if res.scalar_one_or_none():
                    return  # Already tracked

            guild = event.guild
            if not guild:
                return

            start_dt = event.start_time
            end_dt = event.end_time or (start_dt + timedelta(hours=2))
            loc_str = event.location or (
                event.channel.name if event.channel else "Discord"
            )

            # 1. Sync to Google Calendar
            gcal_data = await create_gcal_event(
                name=event.name,
                description=event.description or "",
                start_dt=start_dt,
                end_dt=end_dt,
                location=loc_str,
            )
            gcal_id = gcal_data.get("id") if gcal_data else None

            # 2. Determine broadcast channel
            announcement_channel_id = int(
                os.environ.get("ANNOUNCEMENT_CHANNEL_ID", "0")
            )
            broadcast_channel = None
            if announcement_channel_id:
                broadcast_channel = self.bot.get_channel(announcement_channel_id)
            if not broadcast_channel:
                broadcast_channel = guild.system_channel

            event_url = f"https://discord.com/events/{guild.id}/{event.id}"

            # 3. Render and send broadcast initial announcement
            broadcast_msg = None
            if broadcast_channel:
                try:
                    initial_context = {
                        "event": {
                            "name": event.name,
                            "description": event.description or "",
                            "location": loc_str,
                            "event_url": event_url,
                        },
                        "formatted_date": format_indonesian_date(start_dt),
                        "formatted_time": format_time_wib(start_dt),
                        "role_mention": None,
                    }
                    rendered_msg = render_event_template(
                        "events/broadcast_initial.j2", initial_context
                    )
                    broadcast_msg = await broadcast_channel.send(
                        rendered_msg,
                        allowed_mentions=discord.AllowedMentions(
                            everyone=True, roles=True, users=True
                        ),
                    )
                except Exception as b_err:
                    logger.error(
                        f"Failed to send initial broadcast on_scheduled_event_create: {b_err}"
                    )

            ev_type = detect_event_type(event.name)
            now_utc = datetime.now(timezone.utc)
            pruned_intervals = prune_reminder_intervals(
                start_dt, now_utc, event_type=ev_type
            )

            # Classify event
            classification = await default_event_classifier.classify_event(
                event.name, event.description, gcal_id=gcal_id
            )

            # 4. Save to PostgreSQL DB
            async with async_session() as session:
                db_event = ScheduledEvent(
                    id=event.id,
                    gcal_event_id=gcal_id,
                    guild_id=guild.id,
                    broadcast_channel_id=broadcast_channel.id
                    if broadcast_channel
                    else guild.id,
                    broadcast_message_id=broadcast_msg.id if broadcast_msg else None,
                    name=event.name,
                    description=event.description,
                    location=loc_str,
                    start_time=start_dt,
                    end_time=end_dt,
                    event_url=event_url,
                    classification_label=classification.label,
                    is_discord_event=True,
                    reminder_intervals=pruned_intervals,
                    reminders_sent=[],
                    template_name="default_reminder.j2",
                    is_active=True,
                )
                session.add(db_event)
                await session.commit()
            logger.info(
                f"Successfully synced UI-created Discord event '{event.name}' ({classification.label}) to DB and Google Calendar."
            )
        except Exception as err:
            logger.error(
                f"Error handling on_scheduled_event_create: {err}", exc_info=True
            )

    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ):
        """Gateway listener to sync event updates (anti data-drift)"""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(ScheduledEvent).where(ScheduledEvent.id == after.id)
                )
                db_event = result.scalar_one_or_none()
                if not db_event:
                    return

                if after.status in (
                    discord.EventStatus.completed,
                    discord.EventStatus.cancelled,
                ):
                    db_event.is_active = False
                else:
                    # Check if event was just transitioned to active
                    was_not_active = before.status != discord.EventStatus.active
                    is_now_active = after.status == discord.EventStatus.active
                    sent_list = list(db_event.reminders_sent or [])

                    if (
                        was_not_active
                        and is_now_active
                        and 0 not in sent_list
                        and (db_event.id, 0) not in self._reminded_cache
                    ):
                        # Claim lock immediately in cache and DB
                        self._reminded_cache.add((db_event.id, 0))
                        sent_list.append(0)
                        db_event.reminders_sent = sent_list
                        flag_modified(db_event, "reminders_sent")
                        await session.commit()

                        channel = self.bot.get_channel(db_event.broadcast_channel_id)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(
                                    db_event.broadcast_channel_id
                                )
                            except Exception:
                                channel = None

                        if channel:
                            role_mention_str = (
                                f"<@&{db_event.target_role_id}>"
                                if db_event.target_role_id
                                else None
                            )
                            start_ctx = {
                                "event": {
                                    "name": db_event.name,
                                    "location": db_event.location,
                                    "event_url": db_event.event_url,
                                },
                                "role_mention": role_mention_str,
                            }
                            try:
                                fallback_start = render_event_template(
                                    "events/broadcast_started.j2", start_ctx
                                )
                                start_text = await generate_dynamic_event_message(
                                    event_type="started",
                                    event_name=db_event.name,
                                    event_description=db_event.description,
                                    fallback_text=fallback_start,
                                    role_mention=role_mention_str,
                                    timeout_sec=120.0,
                                )
                                await channel.send(
                                    start_text,
                                    allowed_mentions=discord.AllowedMentions(
                                        everyone=True, roles=True, users=True
                                    ),
                                )
                                logger.info(
                                    f"Broadcast started dikirim via on_scheduled_event_update untuk '{db_event.name}'"
                                )
                            except Exception as start_err:
                                logger.error(
                                    f"Failed to send started broadcast on update: {start_err}"
                                )

                    db_event.name = after.name
                    db_event.description = after.description
                    if after.location:
                        db_event.location = after.location
                    elif after.channel:
                        db_event.location = after.channel.name

                    if after.start_time and after.start_time != db_event.start_time:
                        db_event.start_time = after.start_time
                        # Re-prune intervals for updated start time
                        db_event.reminder_intervals = prune_reminder_intervals(
                            after.start_time
                        )
                        # Reset sent reminders whose triggers are now in the future
                        now_utc = datetime.now(timezone.utc)
                        db_event.reminders_sent = [
                            i
                            for i in (db_event.reminders_sent or [])
                            if now_utc >= (after.start_time - timedelta(minutes=i))
                        ]
                        flag_modified(db_event, "reminder_intervals")
                        flag_modified(db_event, "reminders_sent")

                    if after.end_time:
                        db_event.end_time = after.end_time

                await session.commit()
        except Exception as e:
            logger.error(f"Error handling scheduled event update: {e}")

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        """Gateway listener to stop reminders when an event is deleted"""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(ScheduledEvent).where(ScheduledEvent.id == event.id)
                )
                db_event = result.scalar_one_or_none()
                if db_event:
                    db_event.is_active = False
                    await session.commit()
        except Exception as e:
            logger.error(f"Error handling scheduled event delete: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if after.channel and after.channel.id == self.GENERATOR_CHANNEL_ID:
            guild = member.guild
            category = after.channel.category

            overwrites = category.overwrites.copy() if category else {}

            overwrites[member] = discord.PermissionOverwrite(
                manage_channels=True,
                move_members=True,
                manage_permissions=True,
            )

            try:
                temp_channel = await guild.create_voice_channel(
                    name=f"🔒 {member.display_name}'s Room",
                    category=category,
                    overwrites=overwrites,
                )

                self.active_temp_channels.add(temp_channel.id)

                await member.move_to(temp_channel)

            except discord.HTTPException as e:
                logger.error(f"Failed to create temp channel: {e}")
                return

        if before.channel and before.channel.id in self.active_temp_channels:
            if len(before.channel.members) == 0:
                try:
                    self.active_temp_channels.remove(before.channel.id)
                    await before.channel.delete(reason="Temp channel empty.")
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    logger.error(f"Failed to delete temp channel: {e}")


async def setup(bot):
    await bot.add_cog(ServerEvents(bot))
