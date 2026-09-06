"""Goal intelligence worker — same poll-loop pattern as document/intelligence workers.

Supports two job kinds:
  goal_intelligence   — run full 4-stage pipeline (Research→Assessment→Gaps→Planning)
  assessment_refresh  — skip Research, re-run Assessment→Gaps→Planning only (Vault update)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.platform.database.db import get_session_factory
from pai.platform.llm.gateway import LLMGateway
from pai.platform.jobs.lease import MAX_ATTEMPTS, apply_failure
from pai.domains.goals.models import Goal, GoalIntelligence, GoalJob

logger = logging.getLogger(__name__)

_GOAL_JOB_LOCK_NS = 92847361
_CLAIM_SQL = """
SELECT c.id
FROM goal_jobs AS c
WHERE c.status = 'pending'
  AND c.available_at <= :now
  AND NOT EXISTS (
      SELECT 1
      FROM goal_jobs AS p
      WHERE p.goal_id = c.goal_id
        AND p.status = 'processing'
  )
  AND pg_try_advisory_xact_lock(:lock_ns, hashtext(c.goal_id::text))
ORDER BY c.created_at
FOR UPDATE SKIP LOCKED
LIMIT 1
"""


async def claim_next_goal_job(session: AsyncSession) -> GoalJob | None:
    from sqlalchemy import text

    cutoff = datetime.now(UTC) - timedelta(seconds=600)
    await session.execute(
        update(GoalJob)
        .where(GoalJob.status == "processing", GoalJob.locked_at <= cutoff)
        .values(status="pending", locked_at=None)
    )
    now = datetime.now(UTC)
    result = await session.execute(
        text(_CLAIM_SQL), {"now": now, "lock_ns": _GOAL_JOB_LOCK_NS}
    )
    job_id = result.scalar_one_or_none()
    if job_id is None:
        await session.commit()
        return None
    job = await session.get(GoalJob, job_id)
    if job is None:
        await session.commit()
        return None
    job.status = "processing"
    job.locked_at = now
    job.attempts += 1
    await session.commit()
    await session.refresh(job)
    return job


def _build_vault_snapshot(person_records: dict) -> dict:
    """Extract counselor-safe profile snapshot for assessment stage."""
    snap: dict = {}
    # Keys must match load_typed_profile_records() (camelCase for work).
    for key in (
        "educations",
        "skills",
        "workExperiences",
        "projects",
        "certifications",
        "goals",
    ):
        records = person_records.get(key) or []
        if records:
            snap[key] = records[:5]
    for key in (
        "application.test_scores", "application.study_country",
        "finance.funding_status", "demographics.nationality",
        "location.current_country", "identity.current_status",
        "education.highest_level",
    ):
        val = person_records.get("sparseFields", {}).get(key)
        if val:
            snap[key] = val
    return snap


async def _load_goal_and_intel(
    session: AsyncSession, goal_id: uuid.UUID
) -> tuple[Goal | None, GoalIntelligence | None]:
    goal = await session.get(Goal, goal_id)
    if goal is None:
        return None, None
    intel_row = await session.execute(
        select(GoalIntelligence).where(GoalIntelligence.goal_id == goal_id)
    )
    intel = intel_row.scalar_one_or_none()
    return goal, intel


async def _save_intelligence(
    session: AsyncSession,
    goal: Goal,
    intel: GoalIntelligence | None,
    result: dict,
) -> GoalIntelligence:
    if intel is None:
        intel = GoalIntelligence(
            goal_id=goal.id,
            person_id=goal.person_id,
        )
        session.add(intel)
    intel.research = result.get("research") or {}
    intel.assessment = result.get("assessment") or {}
    intel.gaps = result.get("gaps") or []
    intel.plan = result.get("plan") or []
    intel.counselor_brief = result.get("counselor_brief") or ""
    intel.status = result.get("status", "ready")
    intel.freshness = result.get("freshness") or {}
    intel.updated_at = datetime.now(UTC)
    goal.intelligence_status = intel.status
    return intel


async def process_goal_job(
    session: AsyncSession,
    settings: Settings,
    job: GoalJob,
    gateway: LLMGateway,
) -> None:
    """Run the pipeline for this job and persist results."""
    from pai.intelligences.goals.pipeline import run_full_pipeline, run_assessment_stage, run_gaps_stage, run_planning_stage, build_counselor_brief
    from pai.domains.student.person.profile_snapshot import load_typed_profile_records
    from pai.domains.student.vault.service import VaultService
    from sqlalchemy.orm import selectinload
    from pai.domains.student.person.models import Person

    job_id = job.id
    goal_id = job.goal_id
    goal, intel = await _load_goal_and_intel(session, goal_id)
    if goal is None:
        logger.warning("GoalJob %s references missing goal %s", job_id, goal_id)
        job.status = "failed"
        job.last_error = "Goal not found"
        return

    person = await session.execute(
        select(Person).options(selectinload(Person.vault)).where(Person.id == goal.person_id)
    )
    person_row = person.scalar_one_or_none()
    if person_row is None:
        job.status = "failed"
        job.last_error = "Person not found"
        return

    typed_records = await load_typed_profile_records(session, goal.person_id)
    vault_svc = VaultService(settings)
    unified = await vault_svc.get_unified_vault(
        session, person_row, include_sensitive=False, typed_records=typed_records
    )
    vault_snapshot = _build_vault_snapshot({**typed_records, "sparseFields": unified.get("sparseFields") or {}})

    anchors = {
        **(goal.anchors or {}),
        "goal_type": goal.goal_type,
    }
    for key in ("degree_level", "program", "target_country", "role", "target_company", "intake_year", "intake_term", "budget_range"):
        val = getattr(goal, key, None)
        if val:
            anchors[key] = val

    kind = job.kind or "goal_intelligence"

    if (settings.enable_integrated_goal_analysis and kind == "assessment_refresh"
            and intel is not None and intel.research):
        from pai.intelligences.goals.integrated import analyze_goal

        result = await analyze_goal(
            gateway, settings=settings, goal_type=goal.goal_type, goal_title=goal.title,
            vault_snapshot=vault_snapshot, research=intel.research,
        )
    elif kind == "assessment_refresh" and intel is not None and intel.research:
        # Reuse existing research; re-run Assessment→Gaps→Planning
        from pai.intelligences.goals.pipeline import run_assessment_stage, run_gaps_stage, run_planning_stage, build_counselor_brief
        assessment = await run_assessment_stage(
            gateway,
            research=intel.research,
            vault_snapshot=vault_snapshot,
            goal_type=goal.goal_type,
            goal_title=goal.title,
        )
        gaps = await run_gaps_stage(
            gateway,
            assessment=assessment,
            goal_type=goal.goal_type,
            goal_title=goal.title,
        )
        plan = await run_planning_stage(
            gateway,
            gaps=gaps,
            research=intel.research,
            goal_type=goal.goal_type,
            goal_title=goal.title,
        )
        brief = await build_counselor_brief(
            gateway,
            goal_title=goal.title,
            goal_type=goal.goal_type,
            research=intel.research,
            assessment=assessment,
            gaps=gaps,
            plan=plan,
        )
        result = {
            "research": intel.research,
            "assessment": assessment,
            "gaps": gaps,
            "plan": plan,
            "counselor_brief": brief,
            "status": "ready",
            "freshness": {"computed_at": datetime.now(UTC).isoformat()},
        }
    else:
        result = await run_full_pipeline(
            gateway,
            goal_type=goal.goal_type,
            goal_title=goal.title,
            anchors=anchors,
            vault_snapshot=vault_snapshot,
            settings=settings,
        )

    await _save_intelligence(session, goal, intel, result)
    # Denormalize research university/company options onto goal.anchors so the
    # sync resolver can match "focus on Bologna" / "TUM" without creating dupes.
    options = (result.get("research") or {}).get("options") or []
    if isinstance(options, list) and options:
        unis = [
            str(item).strip()
            for item in options
            if isinstance(item, (str, int, float)) and str(item).strip()
        ]
        if unis:
            merged = {**(goal.anchors or {}), "target_universities": unis[:8]}
            goal.anchors = merged
    job.status = "completed"
    job.locked_at = None


async def run_goal_worker_once(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    factory = get_session_factory(settings)
    async with factory() as session:
        job = await claim_next_goal_job(session)
        if job is None:
            return False
        job_id = job.id
        gateway = LLMGateway(settings)
        try:
            await process_goal_job(session, settings, job, gateway)
            await session.commit()
        except Exception as exc:
            logger.exception("Goal job failed job=%s", job_id)
            await session.rollback()
            fresh = await session.get(GoalJob, job_id)
            if fresh is not None:
                apply_failure(fresh, exc, max_attempts=MAX_ATTEMPTS)
                fresh_goal = await session.get(Goal, fresh.goal_id)
                if fresh_goal is not None:
                    fresh_goal.intelligence_status = "failed" if fresh.status == "failed" else "pending"
                await session.commit()
        finally:
            await gateway.aclose()
    return True


async def goal_worker_loop(settings: Settings, stop_event: asyncio.Event) -> None:
    """Background poll loop for goal intelligence jobs."""
    while not stop_event.is_set():
        try:
            processed = await run_goal_worker_once(settings)
            if not processed:
                await asyncio.sleep(2.0)
        except ProgrammingError as exc:
            if "goal_jobs" in str(exc) and "does not exist" in str(exc):
                logger.warning("goal_jobs table missing; run: alembic upgrade head")
                await asyncio.sleep(15.0)
                continue
            logger.exception("Goal worker iteration error")
            await asyncio.sleep(5.0)
        except OSError as exc:
            logger.warning("Goal worker DB unreachable (%s); retrying…", exc)
            await asyncio.sleep(15.0)
        except Exception as exc:
            msg = str(exc).lower()
            if any(kw in msg for kw in ("getaddrinfo", "connect", "timeout")):
                logger.warning("Goal worker connection issue (%s); retrying…", exc)
                await asyncio.sleep(15.0)
            else:
                logger.exception("Goal worker iteration error")
                await asyncio.sleep(5.0)
