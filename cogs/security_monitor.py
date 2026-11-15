"""Scan accessible Discord channels for likely exposed credentials."""

import logging

import discord
from discord.ext import commands

from services.security.credential_scanner import CredentialFinding, scan_credentials

logger = logging.getLogger("security_monitor")
REVIEW_TIMEOUT_SECONDS = 900


def _message_text(message: discord.Message) -> str:
    parts = [message.content]
    for embed in message.embeds:
        parts.extend(
            value
            for value in (
                embed.title,
                embed.description,
                embed.url,
                embed.footer.text if embed.footer else None,
                embed.author.name if embed.author else None,
                embed.author.url if embed.author else None,
                *(field.name for field in embed.fields),
                *(field.value for field in embed.fields),
            )
            if value
        )
    return "\n".join(parts)


def _finding_labels(findings: list[CredentialFinding]) -> str:
    return ", ".join(
        sorted({finding.category.replace("_", " ") for finding in findings})
    )


def _message_source_label(message: discord.Message) -> str:
    if getattr(message, "webhook_id", None) is not None:
        webhook_name = discord.utils.escape_markdown(message.author.display_name)
        return f"webhook **{webhook_name}**"
    if getattr(message.author, "bot", False):
        bot_name = discord.utils.escape_markdown(message.author.display_name)
        return f"bot **{bot_name}**"
    return f"<@{message.author.id}>"


class CredentialReviewView(discord.ui.View):
    def __init__(self, source_message: discord.Message):
        super().__init__(timeout=REVIEW_TIMEOUT_SECONDS)
        self.source_message = source_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.send_message(
                "This action is only available to server members.", ephemeral=True
            )
            return False

        is_author = interaction.user.id == self.source_message.author.id
        can_moderate = interaction.user.guild_permissions.manage_messages
        if is_author or can_moderate:
            return True

        await interaction.response.send_message(
            "Only the message author or a moderator with Manage Messages can review this alert.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(
        label="Delete message", style=discord.ButtonStyle.danger, emoji="🗑️"
    )
    async def delete_message(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        try:
            await self.source_message.delete()
        except discord.NotFound:
            status = "The original message was already deleted."
        except discord.Forbidden:
            await interaction.response.send_message(
                "Nexo cannot delete that message. It needs Manage Messages in this channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                "Discord could not delete the message. A moderator can remove it manually.",
                ephemeral=True,
            )
            return
        else:
            status = "The original message was deleted. Rotate the credential if it was valid."

        logger.info(
            "Credential alert action=delete source_message_id=%s actor_id=%s",
            self.source_message.id,
            interaction.user.id,
        )
        await interaction.response.edit_message(
            content=f"🗑️ {status}", embed=None, view=None
        )

    @discord.ui.button(
        label="Mark as false positive", style=discord.ButtonStyle.secondary
    )
    async def mark_false_positive(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        logger.info(
            "Credential alert action=false_positive source_message_id=%s actor_id=%s",
            self.source_message.id,
            interaction.user.id,
        )
        await interaction.response.edit_message(
            content="✅ Marked as a false positive. The original message was left unchanged.",
            embed=None,
            view=None,
        )


class SecurityMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        findings = scan_credentials(_message_text(message))
        if not findings:
            return

        labels = _finding_labels(findings)
        source_label = _message_source_label(message)
        source_message_id = message.id
        source_author_id = message.author.id
        has_high_confidence = any(finding.confidence == "high" for finding in findings)

        if has_high_confidence:
            try:
                await message.delete()
            except discord.NotFound:
                logger.info(
                    "High-confidence credential message already deleted (message_id=%s)",
                    source_message_id,
                )
                return
            except discord.Forbidden:
                logger.warning(
                    "Cannot remove high-confidence credential message (message_id=%s)",
                    source_message_id,
                )
                await self._send_review_alert(message, labels, findings)
                return
            except discord.HTTPException:
                logger.warning(
                    "Discord failed to remove high-confidence credential message (message_id=%s)",
                    source_message_id,
                )
                await self._send_review_alert(message, labels, findings)
                return

            logger.warning(
                "Removed high-confidence credential message (message_id=%s, author_id=%s, categories=%s)",
                source_message_id,
                source_author_id,
                labels,
            )
            try:
                await message.channel.send(
                    "🚨 Nexo removed a message from "
                    f"{source_label} that appeared to contain a credential ({labels}). "
                    "Rotate it if it was valid.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not post credential-removal notice (message_id=%s)",
                    source_message_id,
                )
            return

        await self._send_review_alert(message, labels, findings)

    async def _send_review_alert(
        self,
        message: discord.Message,
        labels: str,
        findings: list[CredentialFinding],
    ) -> None:
        source_label = _message_source_label(message)
        confidence = max(
            (finding.confidence for finding in findings),
            key={"low": 0, "medium": 1, "high": 2}.get,
        )
        embed = discord.Embed(
            title="Possible credential exposure",
            description=(
                f"Nexo found a possible {labels} in this message. "
                f"Source: {source_label}. "
                f"Confidence: **{confidence}**. The message was not removed. "
                "Choose an action below."
            ),
            color=discord.Color.orange(),
        )
        try:
            await message.channel.send(
                embed=embed,
                view=CredentialReviewView(message),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning(
                "Could not post credential review alert (message_id=%s)", message.id
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(SecurityMonitor(bot))
