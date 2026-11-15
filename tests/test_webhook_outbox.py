from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid7

import pytest
from sqlalchemy import delete

from db.models import GitHubWebhookDelivery, WebhookOutboxJob
from db.repositories.webhook_outbox import (
    build_effect_key,
    claim_next_webhook_job,
    complete_webhook_job,
    fail_or_retry_webhook_job,
)
from db.session import async_session
from tests.conftest import requires_postgres
from utils.gateway_server import _dispatch_outbox_job


class AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_):
        return False


def test_effect_key_is_stable_and_scoped_to_job_type():
    payload = {"repo": "ksm-aiot-upnvj/nexo", "status": "success"}

    assert build_effect_key("notification", payload) == build_effect_key(
        "notification", {"status": "success", "repo": "ksm-aiot-upnvj/nexo"}
    )
    assert build_effect_key("notification", payload) != build_effect_key(
        "deployment", payload
    )


@pytest.mark.asyncio
async def test_claim_reclaims_expired_processing_lease(monkeypatch):
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=uuid7(),
        delivery_id="delivery-test",
        job_type="discord_notification",
        payload={"message": {}},
        status="processing",
        attempt_count=1,
        locked_until=now - timedelta(seconds=1),
        updated_at=now - timedelta(minutes=10),
    )
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one_or_none=lambda: job)
    )
    session.flush = AsyncMock()
    session.begin.return_value = AsyncContext(session)
    monkeypatch.setattr(
        "db.repositories.webhook_outbox.async_session",
        lambda: AsyncContext(session),
    )

    claimed = await claim_next_webhook_job()

    assert claimed["id"] == job.id
    assert claimed["attempt_count"] == 2
    assert job.status == "processing"
    assert job.locked_until > now
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_is_bounded_and_does_not_persist_error_details(monkeypatch):
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=uuid7(),
        delivery_id="delivery-test",
        attempt_count=2,
        status="processing",
        locked_until=now + timedelta(seconds=10),
        updated_at=now,
        last_error=None,
        available_at=now,
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=job)
    session.begin.return_value = AsyncContext(session)
    monkeypatch.setattr(
        "db.repositories.webhook_outbox.async_session",
        lambda: AsyncContext(session),
    )

    terminal = await fail_or_retry_webhook_job(
        job.id, TimeoutError("request failed with secret URL")
    )

    assert terminal is False
    assert job.status == "pending"
    assert job.attempt_count == 2
    assert job.available_at >= now + timedelta(seconds=2)
    assert job.locked_until is None
    assert job.last_error == "TimeoutError"


@pytest.mark.asyncio
async def test_retry_limit_marks_job_terminally_failed(monkeypatch):
    now = datetime.now(UTC)
    job = SimpleNamespace(
        id=uuid7(),
        delivery_id="delivery-test",
        attempt_count=5,
        status="processing",
        locked_until=now + timedelta(seconds=10),
        updated_at=now,
        last_error=None,
        available_at=now,
    )
    session = MagicMock()
    session.get = AsyncMock(return_value=job)
    session.scalar = AsyncMock(side_effect=[0, 1])
    session.execute = AsyncMock()
    session.begin.return_value = AsyncContext(session)
    monkeypatch.setattr(
        "db.repositories.webhook_outbox.async_session",
        lambda: AsyncContext(session),
    )

    terminal = await fail_or_retry_webhook_job(job.id, RuntimeError("temporary"))

    assert terminal is True
    assert job.status == "failed"
    assert job.last_error == "RuntimeError"
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_deployment_outbox_job_surfaces_portainer_failure_to_retry_worker():
    cog = SimpleNamespace(trigger_docker_compose=AsyncMock())
    bot = SimpleNamespace(get_cog=lambda _: cog)
    job = {
        "job_type": "portainer_deployment",
        "attempt_count": 2,
        "payload": {
            "repo_key": "ksm-aiot-upnvj/nexo",
            "repo_name": "nexo",
            "target_channel_id": 12345,
            "announcement_payload": {"content": "release"},
        },
    }

    await _dispatch_outbox_job(bot, job)

    cog.trigger_docker_compose.assert_awaited_once_with(
        repo_key="ksm-aiot-upnvj/nexo",
        repo_name="nexo",
        target_channel_id=12345,
        announcement_payload={"content": "release"},
        notify_start=False,
        raise_on_failure=True,
    )


@requires_postgres
@pytest.mark.asyncio
async def test_completed_delivery_is_failed_if_another_effect_failed():
    delivery_id = f"test-{uuid7()}"
    failed_job_id = uuid7()
    completed_job_id = uuid7()
    now = datetime.now(UTC)
    async with async_session() as session, session.begin():
        session.add(
            GitHubWebhookDelivery(
                delivery_id=delivery_id,
                event="push",
                status="processing",
                attempt_count=1,
                received_at=now,
                updated_at=now,
            )
        )
        session.add_all(
            [
                WebhookOutboxJob(
                    id=failed_job_id,
                    delivery_id=delivery_id,
                    effect_key="failed-effect",
                    job_type="discord_notification",
                    payload={},
                    status="failed",
                    attempt_count=5,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                ),
                WebhookOutboxJob(
                    id=completed_job_id,
                    delivery_id=delivery_id,
                    effect_key="successful-effect",
                    job_type="discord_notification",
                    payload={},
                    status="processing",
                    attempt_count=1,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )

    try:
        await complete_webhook_job(completed_job_id)
        async with async_session() as session:
            delivery = await session.get(GitHubWebhookDelivery, delivery_id)
            assert delivery is not None
            assert delivery.status == "failed"
    finally:
        async with async_session() as session, session.begin():
            await session.execute(
                delete(WebhookOutboxJob).where(
                    WebhookOutboxJob.delivery_id == delivery_id
                )
            )
            await session.execute(
                delete(GitHubWebhookDelivery).where(
                    GitHubWebhookDelivery.delivery_id == delivery_id
                )
            )
