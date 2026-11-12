import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import aiohttp
import discord
from discord.ext import commands

from config.settings import settings

logger = logging.getLogger("cogs.webhook_deploy")


def build_portainer_webhooks() -> dict[str, str]:
    """Read Portainer webhook URLs from the project configuration."""
    portainer_webhooks: dict[str, str] = {}
    projects_config_path = Path("config/projects.json")
    if projects_config_path.exists():
        try:
            with open(projects_config_path, "r", encoding="utf-8") as f:
                projects_data = json.load(f)
                if isinstance(projects_data, dict):
                    for repo_key, cfg in projects_data.items():
                        if isinstance(cfg, dict):
                            short_name = repo_key.split("/")[-1]
                            p_url = cfg.get("portainer_webhook_url")
                            if p_url:
                                url_clean = str(p_url).strip()
                                portainer_webhooks[repo_key] = url_clean
                                portainer_webhooks[short_name] = url_clean
        except Exception as err:  # noqa: BLE001
            logger.warning(
                f"Could not read deploy config from config/projects.json: {err}"
            )

    return portainer_webhooks


def truncate_output(text: str, limit: int = 1800) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 50] + "\n... (truncated) ..."


def build_discord_view_from_components(
    components_data: list[dict[str, Any]],
) -> discord.ui.View | None:
    """Constructs a discord.ui.View with Link Buttons from JSON component definitions."""
    if not components_data:
        return None
    view = discord.ui.View()
    for row in components_data:
        for comp in row.get("components", []):
            if comp.get("type") == 2 and comp.get("style") == 5:
                emoji_val = None
                if comp.get("emoji") and isinstance(comp["emoji"], dict):
                    emoji_val = comp["emoji"].get("name")
                btn = discord.ui.Button(
                    label=comp.get("label", "Link"),
                    url=comp.get("url"),
                    emoji=emoji_val,
                )
                view.add_item(btn)
    return view if len(view.children) > 0 else None


class WebhookDeployCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.portainer_webhooks = build_portainer_webhooks()

    async def send_discord_notification(
        self,
        payload: dict[str, Any],
        target_channel_id: int | None = None,
        fallback_to_devlogs: bool = True,
    ):
        """Send notification via webhook URL or bot text channel."""
        components_data = payload.get("components", [])
        view = build_discord_view_from_components(components_data)

        if target_channel_id:
            channel = self.bot.get_channel(target_channel_id)
            if channel:
                content = payload.get("content", "")
                embeds_data = payload.get("embeds", [])
                discord_embeds = []

                for emb in embeds_data:
                    try:
                        embed = discord.Embed.from_dict(emb)
                        discord_embeds.append(embed)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Failed to parse embed dict: {e}")

                if view:
                    await channel.send(
                        content=content, embeds=discord_embeds, view=view
                    )
                else:
                    await channel.send(content=content, embeds=discord_embeds)
                return
            else:
                logger.warning(
                    f"Target channel ID {target_channel_id} not found in bot, fallback to default."
                )
                if not fallback_to_devlogs:
                    return

        webhook_url = settings.WEBHOOK_DEVLOGS_CHANNEL
        if webhook_url.startswith(("http://", "https://")):
            try:
                async with aiohttp.ClientSession() as session:  # noqa: SIM117
                    async with session.post(
                        webhook_url, json=payload, timeout=10
                    ) as resp:
                        if resp.status not in (200, 204):
                            logger.warning(
                                f"Webhook URL returned non-200 status: {resp.status}"
                            )
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to post to Webhook URL: {e}")
            return

        # Fallback to Channel ID
        channel_id = settings.WEBHOOK_DEVLOGS_CHANNEL_ID
        if not channel_id and webhook_url.isdigit():
            channel_id = int(webhook_url)

        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                content = payload.get("content", "")
                embeds_data = payload.get("embeds", [])
                discord_embeds = []

                for emb in embeds_data:
                    try:
                        embed = discord.Embed.from_dict(emb)
                        discord_embeds.append(embed)
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Failed to parse embed dict: {e}")

                if view:
                    await channel.send(
                        content=content, embeds=discord_embeds, view=view
                    )
                else:
                    await channel.send(content=content, embeds=discord_embeds)
            else:
                logger.warning(f"Could not find Discord channel with ID {channel_id}")
        else:
            logger.warning(
                "No WEBHOOK_DEVLOGS_CHANNEL or WEBHOOK_DEVLOGS_CHANNEL_ID configured."
            )

    async def trigger_portainer_webhook(
        self,
        webhook_url: str,
        target_display: str,
        target_channel_id: int | None = None,
        announcement_payload: dict[str, Any] | None = None,
    ):
        logger.info(f"Triggering Portainer Webhook for {target_display}...")
        is_self_update = "nexo" in target_display.lower()
        if is_self_update:
            pre_payload = {
                "content": (
                    f"🔄 **Self-Update Initiated for {target_display}**\n"
                    "Triggering the Portainer webhook to recreate the Nexo container..."
                ),
            }
            await self.send_discord_notification(
                pre_payload, target_channel_id=target_channel_id
            )
            await asyncio.sleep(2)

        try:
            async with aiohttp.ClientSession() as session:  # noqa: SIM117
                async with session.post(webhook_url, timeout=30) as resp:
                    if resp.status in (200, 204):
                        logger.info(
                            f"Portainer redeploy triggered successfully for {target_display}"
                        )
                        payload = {
                            "content": (
                                f"🚀 **Portainer Redeploy Triggered for {target_display}**\n"
                                "The Portainer webhook was triggered successfully. The container is being updated..."
                            )
                        }
                        await self.send_discord_notification(
                            payload, target_channel_id=target_channel_id
                        )

                        if announcement_payload:
                            await self.send_discord_notification(
                                announcement_payload,
                                target_channel_id=target_channel_id,
                            )
                            devlogs_channel_id = int(
                                settings.WEBHOOK_DEVLOGS_CHANNEL_ID
                            )
                            if (
                                devlogs_channel_id
                                and devlogs_channel_id != target_channel_id
                            ):
                                await self.send_discord_notification(
                                    announcement_payload,
                                    target_channel_id=devlogs_channel_id,
                                )
                    else:
                        text_resp = await resp.text()
                        raise RuntimeError(
                            f"Portainer webhook returned status {resp.status}: {text_resp}"
                        )
        except Exception as e:  # noqa: BLE001
            err_msg = truncate_output(str(e))
            logger.error(f"Portainer webhook trigger failed: {err_msg}")
            fail_payload = {
                "content": (
                    f"❌ **Portainer Deploy Failed**\n"
                    f"Repo: **{target_display}**\n"
                    f"```\n{err_msg}\n```"
                ),
            }
            await self.send_discord_notification(
                fail_payload, target_channel_id=target_channel_id
            )
            devlogs_channel_id = int(settings.WEBHOOK_DEVLOGS_CHANNEL_ID)
            if devlogs_channel_id and devlogs_channel_id != target_channel_id:
                await self.send_discord_notification(
                    fail_payload, target_channel_id=devlogs_channel_id
                )

    async def trigger_docker_compose(
        self,
        repo_key: str,
        repo_name: str = "",
        target_channel_id: int | None = None,
        announcement_payload: dict[str, Any] | None = None,
    ):
        """Standardized deployment dispatcher using Portainer Webhooks."""
        self.portainer_webhooks = build_portainer_webhooks()
        target_display = repo_name or repo_key

        portainer_url = self.portainer_webhooks.get(
            repo_key
        ) or self.portainer_webhooks.get(target_display)
        if portainer_url:
            await self.trigger_portainer_webhook(
                webhook_url=portainer_url,
                target_display=target_display,
                target_channel_id=target_channel_id,
                announcement_payload=announcement_payload,
            )
            return

        logger.info(
            f"No Portainer webhook configured for repo={repo_key}. Broadcasting release announcement directly."
        )
        if announcement_payload:
            await self.send_discord_notification(
                announcement_payload, target_channel_id=target_channel_id
            )
            devlogs_channel_id = int(settings.WEBHOOK_DEVLOGS_CHANNEL_ID)
            if devlogs_channel_id and devlogs_channel_id != target_channel_id:
                await self.send_discord_notification(
                    announcement_payload,
                    target_channel_id=devlogs_channel_id,
                )


async def setup(bot):
    await bot.add_cog(WebhookDeployCog(bot))
