from __future__ import annotations

import asyncio
import logging

from pai.config import Settings, get_settings
from pai.platform.database.db import get_session_factory
from pai.intelligences.counselor.followup import run_intelligence_followup
from pai.platform.llm.gateway import LLMGateway
from pai.platform.jobs.queue import (
    claim_next_person_job,
    mark_job_done,
    mark_job_failed,
    proposals_from_payload,
)
from sqlalchemy.exc import ProgrammingError

logger = logging.getLogger(__name__)


async def run_intelligence_worker_once(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    async with factory() as session:
        job = await claim_next_person_job(session)
        if job is None:
            return False
        if job.conversation_id is None:
            await mark_job_failed(session, job, RuntimeError("job missing conversation_id"))
            return True
        payload = job.payload or {}
        gateway = LLMGateway(settings)
        try:
            await run_intelligence_followup(
                settings=settings,
                gateway=gateway,
                person_id=job.person_id,
                conversation_id=job.conversation_id,
                user_message=str(payload.get("user_message") or ""),
                user_message_id=str(payload.get("user_message_id") or ""),
                extraction_required=bool(payload.get("extraction_required")),
                task_proposals=proposals_from_payload(payload.get("task_proposals")),
                run_id=payload.get("run_id"),
            )
            fresh = await session.get(type(job), job.id)
            if fresh is not None:
                await mark_job_done(session, fresh)
        except Exception as exc:
            logger.exception("Intelligence job failed job=%s person=%s", job.id, job.person_id)
            await session.rollback()
            fresh = await session.get(type(job), job.id)
            if fresh is not None:
                await mark_job_failed(session, fresh, exc)
        finally:
            await gateway.aclose()
    return True


async def intelligence_worker_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await run_intelligence_worker_once(settings)
            if not processed:
                await asyncio.sleep(1.0)
        except ProgrammingError as exc:
            if "person_jobs" in str(exc) and "does not exist" in str(exc):
                logger.warning("person_jobs missing; run: alembic upgrade head")
                await asyncio.sleep(15.0)
                continue
            logger.exception("Intelligence worker iteration error")
            await asyncio.sleep(5.0)
        except OSError as exc:
            logger.warning("Intelligence worker DB unreachable (%s); retrying…", exc)
            await asyncio.sleep(15.0)
        except Exception as exc:
            msg = str(exc).lower()
            if "getaddrinfo" in msg or "connect" in msg or "timeout" in msg:
                logger.warning("Intelligence worker connection issue (%s); retrying…", exc)
                await asyncio.sleep(15.0)
            else:
                logger.exception("Intelligence worker iteration error")
                await asyncio.sleep(5.0)
