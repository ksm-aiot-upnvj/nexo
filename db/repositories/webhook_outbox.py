import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid7

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from config.settings import settings
from db.models import GitHubWebhookDelivery, WebhookOutboxJob
from db.session import async_session

logger = logging.getLogger(__name__)

OUTBOX_MAX_BACKOFF_SECONDS = 300


def build_effect_key(job_type: str, payload: dict[str, Any]) -> str:
    """Return a stable key so webhook redeliveries do not enqueue the same effect twice."""
    serialized = json.dumps(
        {"job_type": job_type, "payload": payload},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def enqueue_webhook_jobs(delivery_id: str, jobs: list[dict[str, Any]]) -> int:
    """Persist notification/deployment side effects before acknowledging a webhook."""
    inserted = 0
    now = datetime.now(UTC)
    async with async_session() as session, session.begin():
        for job in jobs:
            job_type = job["job_type"]
            payload = job["payload"]
            effect_key = build_effect_key(job_type, payload)
            statement = (
                insert(WebhookOutboxJob)
                .values(
                    id=uuid7(),
                    delivery_id=delivery_id,
                    effect_key=effect_key,
                    job_type=job_type,
                    payload=payload,
                    status="pending",
                    attempt_count=0,
                    available_at=now,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_webhook_outbox_delivery_effect")
                .returning(WebhookOutboxJob.id)
            )
            result = await session.execute(statement)
            if result.scalar_one_or_none() is not None:
                inserted += 1
                continue

            # A GitHub redelivery is allowed to revive only a terminally failed effect.
            await session.execute(
                update(WebhookOutboxJob)
                .where(
                    WebhookOutboxJob.delivery_id == delivery_id,
                    WebhookOutboxJob.effect_key == effect_key,
                    WebhookOutboxJob.status == "failed",
                )
                .values(
                    status="pending",
                    attempt_count=0,
                    available_at=now,
                    locked_until=None,
                    updated_at=now,
                    last_error=None,
                )
            )

    return inserted


async def claim_next_webhook_job() -> dict[str, Any] | None:
    """Atomically claim one ready job, reclaiming expired processing leases."""
    now = datetime.now(UTC)
    lease = timedelta(seconds=settings.GITHUB_WEBHOOK_OUTBOX_LEASE_SECONDS)
    async with async_session() as session, session.begin():
        statement = (
            select(WebhookOutboxJob)
            .where(
                or_(
                    (WebhookOutboxJob.status == "pending")
                    & (WebhookOutboxJob.available_at <= now),
                    (WebhookOutboxJob.status == "processing")
                    & (WebhookOutboxJob.locked_until <= now),
                )
            )
            .order_by(WebhookOutboxJob.available_at, WebhookOutboxJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = (await session.execute(statement)).scalar_one_or_none()
        if job is None:
            return None

        job.status = "processing"
        job.attempt_count += 1
        job.locked_until = now + lease
        job.updated_at = now
        await session.flush()
        claimed = {
            "id": job.id,
            "delivery_id": job.delivery_id,
            "job_type": job.job_type,
            "payload": job.payload,
            "attempt_count": job.attempt_count,
        }
    return claimed


async def complete_webhook_job(job_id: UUID) -> None:
    now = datetime.now(UTC)
    async with async_session() as session, session.begin():
        job = await session.get(WebhookOutboxJob, job_id, with_for_update=True)
        if job is None:
            return
        job.status = "sent"
        job.sent_at = now
        job.updated_at = now
        job.locked_until = None
        job.last_error = None

        unfinished = await session.scalar(
            select(func.count())
            .select_from(WebhookOutboxJob)
            .where(
                WebhookOutboxJob.delivery_id == job.delivery_id,
                WebhookOutboxJob.status.in_(("pending", "processing")),
            )
        )
        if unfinished == 0:
            failed = await session.scalar(
                select(func.count())
                .select_from(WebhookOutboxJob)
                .where(
                    WebhookOutboxJob.delivery_id == job.delivery_id,
                    WebhookOutboxJob.status == "failed",
                )
            )
            await session.execute(
                update(GitHubWebhookDelivery)
                .where(GitHubWebhookDelivery.delivery_id == job.delivery_id)
                .values(
                    status="failed" if failed else "accepted",
                    updated_at=now,
                    last_error=(
                        "One or more webhook effects failed" if failed else None
                    ),
                )
            )


async def fail_or_retry_webhook_job(job_id: UUID, error: Exception) -> bool:
    """Schedule bounded exponential retry; return True when the job is terminally failed."""
    now = datetime.now(UTC)
    terminal = False
    async with async_session() as session, session.begin():
        job = await session.get(WebhookOutboxJob, job_id, with_for_update=True)
        if job is None:
            return True

        job.locked_until = None
        job.updated_at = now
        job.last_error = type(error).__name__[:255]
        if job.attempt_count >= settings.GITHUB_WEBHOOK_OUTBOX_MAX_ATTEMPTS:
            job.status = "failed"
            terminal = True
        else:
            delay = min(2 ** max(job.attempt_count - 1, 0), OUTBOX_MAX_BACKOFF_SECONDS)
            job.status = "pending"
            job.available_at = now + timedelta(seconds=delay)

        if terminal:
            unfinished = await session.scalar(
                select(func.count())
                .select_from(WebhookOutboxJob)
                .where(
                    WebhookOutboxJob.delivery_id == job.delivery_id,
                    WebhookOutboxJob.status.notin_(("sent", "failed")),
                )
            )
            has_failed = await session.scalar(
                select(func.count())
                .select_from(WebhookOutboxJob)
                .where(
                    WebhookOutboxJob.delivery_id == job.delivery_id,
                    WebhookOutboxJob.status == "failed",
                )
            )
            if unfinished == 0 and has_failed:
                await session.execute(
                    update(GitHubWebhookDelivery)
                    .where(GitHubWebhookDelivery.delivery_id == job.delivery_id)
                    .values(
                        status="failed",
                        updated_at=now,
                        last_error="One or more webhook effects failed",
                    )
                )
    return terminal


async def get_webhook_outbox_counts() -> dict[str, int]:
    async with async_session() as session:
        rows = await session.execute(
            select(WebhookOutboxJob.status, func.count()).group_by(
                WebhookOutboxJob.status
            )
        )
        return {status: count for status, count in rows.all()}
