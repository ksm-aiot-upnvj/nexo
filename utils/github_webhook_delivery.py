from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert

from db.models import GitHubWebhookDelivery
from db.session import async_session

PROCESSING_LEASE = timedelta(minutes=10)
DELIVERY_RETENTION = timedelta(days=30)


async def claim_delivery(delivery_id: str, event: str) -> bool:
    """Claim a new delivery, or reclaim one whose prior attempt failed/stalled."""
    now = datetime.now(UTC)
    async with async_session() as session:
        await session.execute(
            delete(GitHubWebhookDelivery).where(
                GitHubWebhookDelivery.received_at < now - DELIVERY_RETENTION
            )
        )
        statement = (
            insert(GitHubWebhookDelivery)
            .values(
                delivery_id=delivery_id,
                event=event,
                status="processing",
                attempt_count=1,
                received_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["delivery_id"])
            .returning(GitHubWebhookDelivery.delivery_id)
        )
        claimed = (await session.execute(statement)).scalar_one_or_none()
        if claimed is None:
            reclaim = (
                update(GitHubWebhookDelivery)
                .where(
                    GitHubWebhookDelivery.delivery_id == delivery_id,
                    (GitHubWebhookDelivery.status == "failed")
                    | (
                        (GitHubWebhookDelivery.status == "processing")
                        & (GitHubWebhookDelivery.updated_at < now - PROCESSING_LEASE)
                    ),
                )
                .values(
                    event=event,
                    status="processing",
                    attempt_count=GitHubWebhookDelivery.attempt_count + 1,
                    updated_at=now,
                    last_error=None,
                )
                .returning(GitHubWebhookDelivery.delivery_id)
            )
            claimed = (await session.execute(reclaim)).scalar_one_or_none()
        await session.commit()
        return claimed is not None


async def finish_delivery(delivery_id: str, *, error: bool = False) -> None:
    """Persist the attempt result without storing exception text or secrets."""
    async with async_session() as session:
        await session.execute(
            update(GitHubWebhookDelivery)
            .where(GitHubWebhookDelivery.delivery_id == delivery_id)
            .values(
                status="failed" if error else "accepted",
                updated_at=datetime.now(UTC),
                last_error="Webhook processing failed" if error else None,
            )
        )
        await session.commit()
