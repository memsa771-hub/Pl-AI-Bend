from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

MAX_ATTEMPTS = 3
LEASE_SECONDS = 600


async def reclaim_expired_leases(session: AsyncSession, model, *, lease_seconds: int = LEASE_SECONDS) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    await session.execute(
        update(model)
        .where(model.status == "processing", model.locked_at <= cutoff)
        .values(status="pending", locked_at=None)
    )


def apply_failure(job, exc: BaseException, *, max_attempts: int = MAX_ATTEMPTS) -> None:
    job.last_error = str(exc)[:500]
    job.locked_at = None
    if int(getattr(job, "attempts", 0) or 0) < max_attempts:
        job.status = "pending"
        job.available_at = datetime.now(UTC) + timedelta(seconds=2 ** int(job.attempts or 1))
    else:
        job.status = "failed"
