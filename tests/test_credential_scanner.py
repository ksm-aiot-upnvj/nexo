from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cogs.security_monitor import (
    SecurityMonitor,
    _message_source_label,
    _message_text,
)
from services.security.credential_scanner import scan_credentials


def test_database_url_with_password_is_high_confidence_without_returning_value():
    findings = scan_credentials(
        "postgresql+asyncpg://nexo:example-DB-password-987@database/nexo"
    )

    assert [(finding.category, finding.confidence) for finding in findings] == [
        ("database_connection_url", "high")
    ]
    assert "example-DB-password-987" not in repr(findings)


def test_database_url_without_password_is_ignored():
    assert scan_credentials("postgresql://database/nexo") == []


def test_placeholder_credential_assignment_is_ignored():
    assert scan_credentials("API_KEY=YOUR_API_KEY") == []


def test_entropy_only_checks_values_next_to_credential_fields():
    findings = scan_credentials("API_KEY=Q8mZ2pL9vR4xN7cT5wK3")

    assert len(findings) == 1
    assert findings[0].category == "api_key_value"
    assert findings[0].confidence == "medium"
    assert "Q8mZ2pL9vR4xN7cT5wK3" not in repr(findings)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_SECRET", "cc7eeda860feefd3ad21"),
        ("DISCORD_BOT_TOKEN", "MTU0Mzk2MzQ5MTE4OT"),
        ("API_KEY", "aaaaaaaa1234"),
    ],
)
def test_prefixed_credential_fields_are_flagged_without_entropy_gate(field, value):
    findings = scan_credentials(f'{field}="{value}"')

    assert findings
    assert findings[0].confidence in {"low", "medium"}
    assert value not in repr(findings)


def test_known_token_pattern_is_high_confidence():
    findings = scan_credentials("token=github_pat_" + "A" * 30)

    assert [(finding.category, finding.confidence) for finding in findings] == [
        ("github_token", "high")
    ]


def test_embed_text_is_included_in_scan_input():
    embed = SimpleNamespace(
        title="Container alert",
        description="postgres://nexo:sample-password-456@db/nexo",
        url=None,
        footer=None,
        author=None,
        fields=[],
    )
    message = SimpleNamespace(content="", embeds=[embed])

    assert "postgres://nexo:sample-password-456@db/nexo" in _message_text(message)


def test_source_label_uses_member_mention_or_webhook_name():
    member_message = SimpleNamespace(author=SimpleNamespace(id=321), webhook_id=None)
    webhook_message = SimpleNamespace(
        author=SimpleNamespace(id=654, display_name="Beszel Alert Bot"),
        webhook_id=987,
    )
    bot_message = SimpleNamespace(
        author=SimpleNamespace(id=999, bot=True, display_name="Nexo Test"),
        webhook_id=None,
    )

    assert _message_source_label(member_message) == "<@321>"
    assert _message_source_label(webhook_message) == "webhook **Beszel Alert Bot**"
    assert _message_source_label(bot_message) == "bot **Nexo Test**"


@pytest.mark.asyncio
async def test_high_confidence_webhook_message_is_removed_without_logging_secret(
    caplog,
):
    secret_url = "postgres://nexo:example-db-secret-456@db/nexo"
    channel = SimpleNamespace(send=AsyncMock())
    message = SimpleNamespace(
        guild=object(),
        author=SimpleNamespace(id=321, bot=True, display_name="Beszel Alert Bot"),
        webhook_id=987,
        id=654,
        content="Container log: " + secret_url,
        embeds=[],
        delete=AsyncMock(),
        channel=channel,
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    monitor = SecurityMonitor(bot)

    await monitor.on_message(message)

    message.delete.assert_awaited_once()
    channel.send.assert_awaited_once()
    notice = channel.send.await_args.args[0]
    assert "webhook **Beszel Alert Bot**" in notice
    assert secret_url not in notice
    assert secret_url not in str(channel.send.await_args.kwargs)
    assert secret_url not in caplog.text


@pytest.mark.asyncio
async def test_medium_confidence_alert_identifies_human_without_quoting_message():
    channel = SimpleNamespace(send=AsyncMock())
    source_message = SimpleNamespace(
        guild=object(),
        author=SimpleNamespace(id=321, bot=False),
        webhook_id=None,
        id=654,
        content='GITHUB_SECRET="cc7eeda860feefd3ad21"',
        embeds=[],
        channel=channel,
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=999))

    await SecurityMonitor(bot).on_message(source_message)

    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert "<@321>" in embed.description
    assert "cc7eeda860feefd3ad21" not in embed.description


@pytest.mark.asyncio
async def test_nexo_authored_message_is_scanned_without_alert_loop():
    channel = SimpleNamespace(send=AsyncMock())
    secret = "Abc1234567890"
    source_message = SimpleNamespace(
        guild=object(),
        author=SimpleNamespace(id=999, bot=True, display_name="Nexo Test"),
        webhook_id=None,
        id=654,
        content=f"LLM_API_KEY={secret}",
        embeds=[],
        channel=channel,
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=999))

    await SecurityMonitor(bot).on_message(source_message)

    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert "bot **Nexo Test**" in embed.description
    assert secret not in embed.description
