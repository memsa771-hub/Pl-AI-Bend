from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pai.kernel.contracts.schemas import TaskProposal
from pai.platform.jobs.lease import MAX_ATTEMPTS, apply_failure, reclaim_expired_leases as reclaim_model_leases
from pai.platform.jobs.models import PersonJob
# Distinct from other advisory locks in this database.
_PERSON_JOB_LOCK_NS = 87423091

_CLAIM_SQL = """
SELECT c.id
FROM person_jobs AS c
WHERE c.status = 'pending'
  AND c.available_at <= :now
  AND NOT EXISTS (
      SELECT 1
      FROM person_jobs AS p
      WHERE p.person_id = c.person_id
        AND p.status = 'processing'
  )
  AND pg_try_advisory_xact_lock(:lock_ns, hashtext(c.person_id::text))
ORDER BY c.created_at
FOR UPDATE SKIP LOCKED
LIMIT 1
"""


def _proposals_payload(proposals: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in proposals or []:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump())
        elif isinstance(item, dict):
            out.append(item)
    return out


def proposals_from_payload(raw: list | None) -> list[TaskProposal]:
    return [TaskProposal.model_validate(item) for item in (raw or [])]


def needs_intelligence(*, extraction_required: bool, task_proposals: list | None) -> bool:
    return bool(extraction_required) or bool(task_proposals)


def enqueue_intelligence(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_message: str,
    user_message_id: str,
    extraction_required: bool,
    task_proposals: list | None,
    run_id: str | None,
) -> PersonJob | None:
    """Stage a job on this session. Caller commits (same txn as the assistant message)."""
    if not needs_intelligence(
        extraction_required=extraction_required, task_proposals=task_proposals
    ):
        return None
    job = PersonJob(
        person_id=person_id,
        conversation_id=conversation_id,
        kind="chat_intelligence",
        status="pending",
        payload={
            "user_message": user_message,
            "user_message_id": user_message_id,
            "extraction_required": bool(extraction_required),
            "task_proposals": _proposals_payload(task_proposals),
            "run_id": run_id,
        },
    )
    session.add(job)
    return job


async def reclaim_expired_leases(session: AsyncSession) -> None:
    await reclaim_model_leases(session, PersonJob)


async def claim_next_person_job(session: AsyncSession) -> PersonJob | None:
    """Oldest pending job whose student is not already being processed.

    SKIP LOCKED only protects the job row. Two workers could otherwise claim
    Germany and France for the same student before either commits `processing`.
    `pg_try_advisory_xact_lock` on person_id closes that window for
    `uvicorn --workers N`.
    """
    await reclaim_expired_leases(session)
    now = datetime.now(UTC)
    result = await session.execute(
        text(_CLAIM_SQL),
        {"now": now, "lock_ns": _PERSON_JOB_LOCK_NS},
    )
    job_id = result.scalar_one_or_none()
    if job_id is None:
        await session.commit()
        return None
    job = await session.get(PersonJob, job_id)
    if job is None:
        await session.commit()
        return None
    job.status = "processing"
    job.locked_at = now
    job.attempts += 1
    await session.commit()
    await session.refresh(job)
    return job


async def mark_job_done(session: AsyncSession, job: PersonJob) -> None:
    job.status = "completed"
    job.locked_at = None
    await session.commit()


async def mark_job_failed(session: AsyncSession, job: PersonJob, exc: BaseException) -> None:
    apply_failure(job, exc, max_attempts=MAX_ATTEMPTS)
    await session.commit()
