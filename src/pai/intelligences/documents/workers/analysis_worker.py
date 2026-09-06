"""Document intelligence worker: claim jobs, run analysis, persist via the domain."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.domains.documents.models import DocumentJob
from pai.platform.database.db import get_session_factory
from pai.platform.jobs.lease import apply_failure, reclaim_expired_leases
from pai.platform.llm.gateway import LLMGateway
from pai.platform.storage.supabase import SupabaseStorageProvider

logger = logging.getLogger(__name__)

_DOC_JOB_LOCK_NS = 87423092
_CLAIM_SQL = """
SELECT c.id
FROM document_jobs AS c
WHERE c.status = 'pending'
  AND c.available_at <= :now
  AND NOT EXISTS (
      SELECT 1
      FROM document_jobs AS p
      WHERE p.document_id = c.document_id
        AND p.status = 'processing'
  )
  AND pg_try_advisory_xact_lock(:lock_ns, hashtext(c.document_id::text))
ORDER BY c.created_at
FOR UPDATE SKIP LOCKED
LIMIT 1
"""


async def process_document_job(
    session: AsyncSession,
    settings: Settings,
    job: DocumentJob,
    *,
    storage: SupabaseStorageProvider,
    gateway: LLMGateway,
) -> None:
    from pai.intelligences.documents.pipeline import run_document_analysis

    await run_document_analysis(session, settings, job, storage=storage, gateway=gateway)


async def claim_next_job(session: AsyncSession) -> DocumentJob | None:
    await reclaim_expired_leases(session, DocumentJob)
    now = datetime.now(UTC)
    result = await session.execute(
        text(_CLAIM_SQL),
        {"now": now, "lock_ns": _DOC_JOB_LOCK_NS},
    )
    job_id = result.scalar_one_or_none()
    if job_id is None:
        await session.commit()
        return None
    job = await session.get(DocumentJob, job_id)
    if job is None:
        await session.commit()
        return None
    job.status = "processing"
    job.locked_at = now
    job.attempts += 1
    await session.commit()
    await session.refresh(job)
    return job


async def run_document_worker_once(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    storage = SupabaseStorageProvider(settings)
    gateway = LLMGateway(settings)
    try:
        async with factory() as session:
            job = await claim_next_job(session)
            if job is None:
                return False
            try:
                await process_document_job(session, settings, job, storage=storage, gateway=gateway)
                await session.commit()
            except Exception as exc:
                logger.exception("Document job failed")
                await session.rollback()
                job = await session.get(DocumentJob, job.id)
                if job:
                    apply_failure(job, exc)
                    await session.commit()
        return True
    finally:
        await gateway.aclose()
        await storage.aclose()


async def document_worker_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await run_document_worker_once(settings)
            if not processed:
                await asyncio.sleep(2.0)
        except OSError as exc:
            logger.warning("Document worker DB unreachable (%s); retrying…", exc)
            await asyncio.sleep(15.0)
        except Exception as exc:
            msg = str(exc).lower()
            if "getaddrinfo" in msg or "connect" in msg or "timeout" in msg:
                logger.warning("Document worker connection issue (%s); retrying…", exc)
                await asyncio.sleep(15.0)
            else:
                logger.exception("Document worker iteration error")
                await asyncio.sleep(5.0)
