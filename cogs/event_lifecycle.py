import logging
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.models import ScheduledEvent
from db.session import async_session
from utils.event_manager import (
    generate_dynamic_event_message,
    prune_reminder_intervals,
)
from utils.template_renderer import render_event_template

logger = logging.getLogger("event_lifecycle")


class EventLifecycleMixin:
    @commands.Cog.listener()
    async def on_scheduled_event_update(
        self, before: discord.ScheduledEvent, after: discord.ScheduledEvent
    ):
        """Synchronize Discord event updates with persisted state."""
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
                    was_not_active = before.status != discord.EventStatus.active
                    is_now_active = after.status == discord.EventStatus.active
                    sent_list = list(db_event.reminders_sent or [])

                    if (
                        was_not_active
                        and is_now_active
                        and 0 not in sent_list
                        and (db_event.id, 0) not in self._reminded_cache
                    ):
                        self._reminded_cache.add((db_event.id, 0))
                        broadcast_sent = False

                        channel = self.bot.get_channel(db_event.broadcast_channel_id)
                        if not channel:
                            try:
                                channel = await self.bot.fetch_channel(
                                    db_event.broadcast_channel_id
                                )
                            except Exception:  # noqa: BLE001
                                channel = None

                        if channel:
                            role_mention = (
                                f"<@&{db_event.target_role_id}>"
                                if db_event.target_role_id
                                else None
                            )
                            context = {
                                "event": {
                                    "name": db_event.name,
                                    "location": db_event.location,
                                    "event_url": db_event.event_url,
                                },
                                "role_mention": role_mention,
                            }
                            try:
                                fallback = render_event_template(
                                    "events/broadcast_started.j2", context
                                )
                                text = await generate_dynamic_event_message(
                                    event_type="started",
                                    event_name=db_event.name,
                                    event_description=db_event.description,
                                    fallback_text=fallback,
                                    role_mention=role_mention,
                                    timeout_sec=120.0,
                                )
                                await channel.send(
                                    text,
                                    allowed_mentions=discord.AllowedMentions(
                                        everyone=True, roles=True, users=True
                                    ),
                                )
                                broadcast_sent = True
                            except Exception as error:  # noqa: BLE001
                                logger.error(
                                    "Failed to send started broadcast on update: %s",
                                    error,
                                )
                            finally:
                                if broadcast_sent:
                                    sent_list.append(0)
                                    db_event.reminders_sent = sent_list
                                    flag_modified(db_event, "reminders_sent")
                                    try:
                                        await session.commit()
                                    except Exception:
                                        self._reminded_cache.discard((db_event.id, 0))
                                        raise
                                else:
                                    self._reminded_cache.discard((db_event.id, 0))
                        else:
                            self._reminded_cache.discard((db_event.id, 0))

                    db_event.name = after.name
                    db_event.description = after.description
                    if after.location:
                        db_event.location = after.location
                    elif after.channel:
                        db_event.location = after.channel.name

                    if after.start_time and after.start_time != db_event.start_time:
                        db_event.start_time = after.start_time
                        db_event.reminder_intervals = prune_reminder_intervals(
                            after.start_time
                        )
                        now_utc = datetime.now(UTC)
                        db_event.reminders_sent = [
                            interval
                            for interval in (db_event.reminders_sent or [])
                            if now_utc
                            >= (after.start_time - timedelta(minutes=interval))
                        ]
                        flag_modified(db_event, "reminder_intervals")
                        flag_modified(db_event, "reminders_sent")

                    if after.end_time:
                        db_event.end_time = after.end_time

                await session.commit()
        except Exception as error:  # noqa: BLE001
            logger.error("Error handling scheduled event update: %s", error)

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent):
        """Stop reminders when a Discord event is deleted."""
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(ScheduledEvent).where(ScheduledEvent.id == event.id)
                )
                db_event = result.scalar_one_or_none()
                if db_event:
                    db_event.is_active = False
                    await session.commit()
        except Exception as error:  # noqa: BLE001
            logger.error("Error handling scheduled event deletion: %s", error)
