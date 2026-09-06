from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_ATTEMPTS = 3
LEASE_SECONDS = 600


async def reclaim_expired_leases(session: AsyncSession, model, *, lease_seconds: int = LEASE_SECONDS) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_seconds)
    rows = (await session.execute(select(model).where(
        model.status == "processing", model.locked_at <= cutoff
    ).with_for_update(skip_locked=True))).scalars().all()
    for job in rows:
        job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "pending"
        job.locked_at = None


async def pin_lease(session, job):
    """Hold the attempt row until publication; reclaimers skip live workers."""
    attempt = job.attempts
    current = (await session.execute(select(type(job)).where(
        type(job).id == job.id
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    return current is not None and current.status == "processing" and current.attempts == attempt


def apply_failure(job, exc: BaseException, *, max_attempts: int = MAX_ATTEMPTS) -> None:
    job.last_error = str(exc)[:500]
    job.locked_at = None
    if int(getattr(job, "attempts", 0) or 0) < max_attempts:
        job.status = "pending"
        job.available_at = datetime.now(UTC) + timedelta(seconds=2 ** int(job.attempts or 1))
    else:
        job.status = "failed"


async def load_attempt_for_failure(session, model, job_id, attempt):
    """A rolled-back worker may only fail its own still-current attempt."""
    return (await session.execute(select(model).where(
        model.id == job_id, model.attempts == attempt, model.status == "processing"
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
