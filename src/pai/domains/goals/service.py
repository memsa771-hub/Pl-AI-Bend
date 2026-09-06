"""GoalService — canonical read/write path for all goal mutations.

Design rules enforced here:
- Vault-level facts (IELTS, CGPA) never get written onto a Goal row.
- A university mention alone does not create a goal.
- Goal matching is type + anchor similarity, not string equality.
- create_secondary creates without changing active_goal_id.
- switch_active changes the Conversation pointer only when switching is unambiguous.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.goals.models import Goal, GoalIntelligence, GoalJob
from pai.domains.goals.types import GoalType, GoalWriteAction

logger = logging.getLogger(__name__)

# ── Status constants ──────────────────────────────────────────────────────────

LIFECYCLE_DRAFT = "draft"
LIFECYCLE_ACTIVE = "active"
LIFECYCLE_PAUSED = "paused"
LIFECYCLE_ARCHIVED = "archived"

INTEL_PENDING = "pending"
INTEL_RUNNING = "running"
INTEL_READY = "ready"
INTEL_PARTIAL = "partial"
INTEL_FAILED = "failed"
INTEL_STALE = "stale"

# Vault fields that, when updated, may invalidate goal assessment
VAULT_FIELDS_THAT_AFFECT_GOALS: dict[str, list[str]] = {
    # field_key → which goal_types it affects
    "application.test_scores": ["admission"],
    "education.highest_level": ["admission"],
    "education.records": ["admission"],
    "application.study_country": ["admission"],
    "application.target_universities": ["admission"],
    "identity.current_status": ["admission", "job", "internship"],
    "finance.funding_status": ["admission"],
    "finance.scholarship_interest": ["admission"],
    "demographics.nationality": ["admission", "job", "internship"],
    "location.current_country": ["admission", "job", "internship"],
    # Gap-closing profile facts (live test: these were missing → gaps never refreshed)
    "career.work_history": ["admission", "job", "internship"],
    "career.projects": ["admission", "job", "internship"],
    "career.certifications": ["admission", "job", "internship"],
    "career.skills": ["admission", "job", "internship"],
    "mobility.passport_number": ["admission", "job", "internship"],
}

# Fields that must NOT appear on a Goal row (Vault-level only)
_VAULT_ONLY_KEYS = frozenset(
    {
        "ielts",
        "toefl",
        "gre",
        "gmat",
        "cgpa",
        "gpa",
        "nationality",
        "passport",
    }
)


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _anchor_match_score(goal: Goal, anchors: dict[str, Any]) -> float:
    """Return 0-1 similarity of new anchors against existing goal.

    Matching key types: goal_type, target_country, degree_level, program, role.
    Score ≥ 0.6 = same goal.
    """
    if goal.goal_type != anchors.get("goal_type", goal.goal_type):
        return 0.0
    score = 0.0
    checks: list[tuple[str | None, str | None, float]] = [
        (goal.target_country, anchors.get("target_country"), 0.35),
        (goal.degree_level, anchors.get("degree_level"), 0.25),
        (goal.program, anchors.get("program"), 0.20),
        (goal.role, anchors.get("role"), 0.35),
        (goal.target_company, anchors.get("target_company"), 0.25),
    ]
    for existing, incoming, weight in checks:
        if existing and incoming:
            if _norm(existing) == _norm(incoming):
                score += weight
        elif not existing and not incoming:
            # Both absent — neutral (not penalised)
            pass
    return min(score, 1.0)


def _has_hard_conflict(goal: Goal, anchors: dict[str, Any]) -> bool:
    """True only when a stable anchor is present on both sides and disagrees.

    Missing information is never a conflict — only two explicitly stated,
    differing values for the same dimension count as one. This is what lets a
    vague rephrase ("I want to pursue my masters", "get into a German
    university") keep reinforcing an existing goal ("MS in Germany") instead
    of spawning a duplicate Goal row, while a genuinely different pursuit
    (different country/degree/role stated explicitly) is still caught.
    """
    incoming_type = anchors.get("goal_type")
    if incoming_type and goal.goal_type != incoming_type:
        return True
    pairs: list[tuple[str | None, str | None]] = [
        (goal.target_country, anchors.get("target_country")),
        (goal.degree_level, anchors.get("degree_level")),
        (goal.program, anchors.get("program")),
        (goal.role, anchors.get("role")),
        (goal.target_company, anchors.get("target_company")),
    ]
    return any(
        existing and incoming and _norm(existing) != _norm(incoming)
        for existing, incoming in pairs
    )


def _assert_no_vault_keys(anchors: dict[str, Any]) -> None:
    bad = {k for k in anchors if k in _VAULT_ONLY_KEYS}
    if bad:
        raise ValueError(f"Goal anchors must not contain Vault-level keys: {bad!r}")


# ── Public API ────────────────────────────────────────────────────────────────


async def get_active_goal(session: AsyncSession, person_id: uuid.UUID) -> Goal | None:
    """Return the single active goal for this person, if any."""
    result = await session.execute(
        select(Goal)
        .where(
            Goal.person_id == person_id,
            Goal.lifecycle_status == LIFECYCLE_ACTIVE,
        )
        .order_by(Goal.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_goal_by_id(
    session: AsyncSession, goal_id: uuid.UUID, person_id: uuid.UUID
) -> Goal | None:
    result = await session.execute(
        select(Goal).where(Goal.id == goal_id, Goal.person_id == person_id)
    )
    return result.scalar_one_or_none()


async def list_goals(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    include_archived: bool = False,
) -> list[Goal]:
    q = select(Goal).where(Goal.person_id == person_id)
    if not include_archived:
        q = q.where(Goal.lifecycle_status != LIFECYCLE_ARCHIVED)
    q = q.order_by(Goal.updated_at.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def find_matching_goal(
    session: AsyncSession,
    person_id: uuid.UUID,
    anchors: dict[str, Any],
    *,
    threshold: float = 0.6,
) -> Goal | None:
    """Find an existing (non-archived) goal that is the same pursuit as the incoming anchors."""
    goal_type = anchors.get("goal_type")
    if not goal_type:
        return None
    result = await session.execute(
        select(Goal).where(
            Goal.person_id == person_id,
            Goal.goal_type == goal_type,
            Goal.lifecycle_status != LIFECYCLE_ARCHIVED,
        )
    )
    candidates = result.scalars().all()
    best: Goal | None = None
    best_score = 0.0
    for g in candidates:
        s = _anchor_match_score(g, anchors)
        if s > best_score:
            best_score = s
            best = g
    return best if best_score >= threshold else None


async def create_goal(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    goal_type: str,
    title: str,
    anchors: dict[str, Any],
    lifecycle_status: str = LIFECYCLE_DRAFT,
    source_conversation_id: uuid.UUID | None = None,
    confidence: float | None = None,
) -> Goal:
    """Create a brand-new goal record. Caller commits."""
    _assert_no_vault_keys(anchors)
    goal_type = GoalType.coerce(goal_type).value
    goal = Goal(
        person_id=person_id,
        goal_type=goal_type,
        title=title,
        lifecycle_status=lifecycle_status,
        intelligence_status=INTEL_PENDING,
        status=lifecycle_status,  # keep legacy column in sync
        degree_level=anchors.get("degree_level"),
        program=anchors.get("program"),
        target_country=anchors.get("target_country"),
        target_company=anchors.get("target_company"),
        role=anchors.get("role"),
        intake_year=anchors.get("intake_year"),
        intake_term=anchors.get("intake_term"),
        budget_range=anchors.get("budget_range"),
        anchors={k: v for k, v in anchors.items() if k not in {
            "goal_type", "degree_level", "program", "target_country",
            "target_company", "role", "intake_year", "intake_term", "budget_range",
        }},
        confidence=confidence,
        source_conversation_id=source_conversation_id,
    )
    session.add(goal)
    return goal


async def update_goal_anchors(
    session: AsyncSession,
    goal: Goal,
    anchors: dict[str, Any],
    *,
    confidence: float | None = None,
) -> bool:
    """Merge new anchors into goal. Returns True if anything changed."""
    _assert_no_vault_keys(anchors)
    changed = False
    field_map: list[tuple[str, str]] = [
        ("degree_level", "degree_level"),
        ("program", "program"),
        ("target_country", "target_country"),
        ("target_company", "target_company"),
        ("role", "role"),
        ("intake_year", "intake_year"),
        ("intake_term", "intake_term"),
        ("budget_range", "budget_range"),
    ]
    for anchor_key, model_attr in field_map:
        incoming = anchors.get(anchor_key)
        if incoming and getattr(goal, model_attr) != incoming:
            setattr(goal, model_attr, incoming)
            changed = True
    extra = {k: v for k, v in anchors.items() if k not in {
        "goal_type", "degree_level", "program", "target_country",
        "target_company", "role", "intake_year", "intake_term", "budget_range",
    }}
    merged_extra = {**(goal.anchors or {}), **extra}
    if merged_extra != (goal.anchors or {}):
        goal.anchors = merged_extra
        changed = True
    new_title = anchors.get("title")
    if new_title and goal.title != new_title:
        goal.title = new_title
        changed = True
    if confidence is not None and goal.confidence != confidence:
        goal.confidence = confidence
        changed = True
    return changed


async def activate_goal(
    session: AsyncSession,
    goal: Goal,
    *,
    conversation_id: uuid.UUID | None = None,
) -> None:
    """Mark this goal active, pause siblings, and attach the thread pointer.

    Always sets conversations.active_goal_id when a conversation is known.
    If conversation_id is omitted, falls back to the person's latest active
    conversation so typed-apply / career_interest cannot leave the pointer null.
    """
    prev = await session.execute(
        select(Goal).where(
            Goal.person_id == goal.person_id,
            Goal.lifecycle_status == LIFECYCLE_ACTIVE,
            Goal.id != goal.id,
        )
    )
    for other in prev.scalars():
        other.lifecycle_status = LIFECYCLE_PAUSED
        other.status = LIFECYCLE_PAUSED
    goal.lifecycle_status = LIFECYCLE_ACTIVE
    goal.status = LIFECYCLE_ACTIVE

    resolved_conversation_id = conversation_id
    if resolved_conversation_id is None:
        from pai.domains.conversations.service import get_latest_active_conversation

        conv = await get_latest_active_conversation(session, goal.person_id)
        resolved_conversation_id = conv.id if conv is not None else None
    if resolved_conversation_id is not None:
        await switch_conversation_active_goal(session, resolved_conversation_id, goal.id)


async def upsert_goal_from_anchors(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    goal_type: str,
    title: str,
    anchors: dict[str, Any],
    source_conversation_id: uuid.UUID | None = None,
    confidence: float | None = None,
    activate: bool = True,
    create_if_new: bool = True,
) -> tuple[Goal | None, str]:
    """
    Find-or-create. Returns (goal, GoalWriteAction value).
    Switch is decided by the resolver when pointing at a different existing goal.
    """
    goal_type = GoalType.coerce(goal_type).value
    full_anchors = {"goal_type": goal_type, **anchors}
    existing = await find_matching_goal(session, person_id, full_anchors)

    if existing is not None:
        changed = await update_goal_anchors(session, existing, full_anchors, confidence=confidence)
        if activate:
            await activate_goal(
                session, existing, conversation_id=source_conversation_id
            )
        if changed:
            existing.intelligence_status = INTEL_STALE
        return existing, GoalWriteAction.REINFORCE.value

    if not create_if_new:
        return None, GoalWriteAction.NONE.value

    goal = await create_goal(
        session,
        person_id,
        goal_type=goal_type,
        title=title,
        anchors=anchors,
        lifecycle_status=LIFECYCLE_ACTIVE if activate else LIFECYCLE_DRAFT,
        source_conversation_id=source_conversation_id,
        confidence=confidence,
    )
    if activate:
        await activate_goal(session, goal, conversation_id=source_conversation_id)
    action = GoalWriteAction.CREATE if activate else GoalWriteAction.CREATE_SECONDARY
    return goal, action.value


async def switch_conversation_active_goal(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    goal_id: uuid.UUID,
) -> None:
    """Point the thread's active_goal_id at this goal."""
    from pai.domains.conversations.models import Conversation

    conv = await session.get(Conversation, conversation_id)
    if conv is not None:
        conv.active_goal_id = goal_id


async def get_conversation_active_goal(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    person_id: uuid.UUID,
) -> Goal | None:
    """Return the goal currently active in this conversation thread."""
    from pai.domains.conversations.models import Conversation

    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.active_goal_id is None:
        return None
    return await get_goal_by_id(session, conv.active_goal_id, person_id)


async def get_goal_intelligence(
    session: AsyncSession,
    goal_id: uuid.UUID,
) -> GoalIntelligence | None:
    result = await session.execute(
        select(GoalIntelligence).where(GoalIntelligence.goal_id == goal_id)
    )
    return result.scalar_one_or_none()


async def enqueue_goal_intelligence_job(
    session: AsyncSession,
    goal: Goal,
    *,
    kind: str = "goal_intelligence",
    force: bool = False,
) -> GoalJob | None:
    """
    Stage a goal intelligence job. Idempotent: skip if a pending/running job
    already exists for this goal unless force=True.
    """
    if not force:
        existing = await session.execute(
            select(GoalJob).where(
                GoalJob.goal_id == goal.id,
                GoalJob.status.in_(["pending", "processing", "running"]),
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None
    job = GoalJob(
        goal_id=goal.id,
        person_id=goal.person_id,
        kind=kind,
        status="pending",
        payload={"goal_id": str(goal.id), "kind": kind},
    )
    session.add(job)
    return job


async def mark_intelligence_stale_for_vault_update(
    session: AsyncSession,
    person_id: uuid.UUID,
    vault_field_key: str,
) -> list[Goal]:
    """
    When a Vault field changes, mark affected goal summaries stale and re-enqueue.
    Returns goals that were touched.
    """
    affected_types = VAULT_FIELDS_THAT_AFFECT_GOALS.get(vault_field_key, [])
    if not affected_types:
        return []

    result = await session.execute(
        select(Goal).where(
            Goal.person_id == person_id,
            Goal.goal_type.in_(affected_types),
            Goal.lifecycle_status.in_([LIFECYCLE_ACTIVE, LIFECYCLE_PAUSED]),
        )
    )
    goals = list(result.scalars().all())
    for goal in goals:
        goal.intelligence_status = INTEL_STALE
        await enqueue_goal_intelligence_job(
            session, goal, kind="assessment_refresh", force=False
        )
    return goals


def goal_to_public(goal: Goal | None) -> dict[str, Any] | None:
    if goal is None:
        return None
    mode = "pursuing" if goal.lifecycle_status == LIFECYCLE_ACTIVE else (goal.lifecycle_status or "active")
    return {
        "intent": goal.title,
        "mode": mode,
        "goalType": goal.goal_type,
        "goalId": str(goal.id),
        "reason": None,
        "updatedAt": goal.updated_at.isoformat() if goal.updated_at else None,
    }


async def goal_fact_lines(session: AsyncSession, person_id: uuid.UUID) -> list[str]:
    """Counselor-facing goal lines. Goal domain is the source of truth.

    A non-current Goal row is only ever described as "previous ... do not
    keep executing" when it was genuinely superseded (lifecycle PAUSED).
    Draft/secondary rows are surfaced separately and clearly labeled instead
    of being lumped in as "previous". A row whose title is really the same
    pursuit as the current goal (leftover duplicate data) is never shown at
    all, to avoid resurfacing pre-fix duplicates as noise.
    """
    current = await get_active_goal(session, person_id)
    current_key = _norm(current.title) if current is not None else None
    others = [
        g
        for g in await list_goals(session, person_id, include_archived=True)
        if current is None or g.id != current.id
    ]
    lines: list[str] = []
    if current is not None:
        lines.append(f"Current goal ({current.goal_type}): {current.title}")

    seen_keys: set[str] = {current_key} if current_key else set()
    paused_shown = False
    exploring_shown = 0
    for g in others:
        key = _norm(g.title)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        if g.lifecycle_status == LIFECYCLE_PAUSED and not paused_shown:
            lines.append(
                f"Previous goal: {g.title} — superseded, do not keep executing this plan"
            )
            paused_shown = True
        elif g.lifecycle_status != LIFECYCLE_ARCHIVED and exploring_shown < 2:
            lines.append(f"Secondary/exploring goal: {g.title}")
            exploring_shown += 1
    return lines
