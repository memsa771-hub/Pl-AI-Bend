"""Goal resolver — cheap, synchronous, no extra LLM call on the chat path.

Decision logic:
  1. GoalExtract from fact extraction (already computed) tells us kind/intent/mode.
  2. Only life_aim with confidence signals trigger goal writes.
  3. University / title containment reinforces an existing goal (no duplicate).
  4. Return action: create | create_secondary | switch | reinforce | none
  5. create_secondary = second goal without changing active_goal_id.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.goals.models import Goal
from pai.domains.goals.service import (
    activate_goal,
    enqueue_goal_intelligence_job,
    find_matching_goal,
    get_conversation_active_goal,
    list_goals,
    upsert_goal_from_anchors,
)
from pai.domains.goals.types import GoalType, GoalWriteAction

_LIFE_AIM = "life_aim"

@dataclass(frozen=True)
class GroundedLifeAim:
    intent: str
    mode: str
    supersedes: bool
    evidence: str


@dataclass
class ResolverResult:
    action: str  # GoalWriteAction value
    goal: Goal | None
    intelligence_enqueued: bool


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().casefold()


def _span_in_message(span: str, source: str) -> bool:
    evidence = _fold(span)
    return bool(evidence and evidence in _fold(source))


def grounded_life_aim(text: str, llm_goal: Any | None) -> GroundedLifeAim | None:
    """LLM classified life_aim only if evidence is a span of the student text."""
    if llm_goal is None:
        return None
    if (getattr(llm_goal, "kind", None) or "none") != _LIFE_AIM:
        return None
    evidence = (getattr(llm_goal, "evidence_text", None) or "").strip()
    intent = (getattr(llm_goal, "intent", None) or "").strip() or evidence
    if not intent:
        return None
    span = evidence or intent
    if not _span_in_message(span, text):
        return None
    mode = getattr(llm_goal, "mode", None)
    if mode not in ("pursuing", "exploring"):
        mode = "pursuing"
    return GroundedLifeAim(
        intent=intent[:240],
        mode=mode,
        supersedes=bool(getattr(llm_goal, "supersedes_previous", False)),
        evidence=span[:240],
    )


def _classify_goal_type(intent: str, anchors: dict[str, Any], *, llm_goal: Any = None) -> str:
    """Prefer LLM GoalExtract.goal_type; tiny keyword fallback only."""
    hinted = getattr(llm_goal, "goal_type", None) if llm_goal is not None else None
    if hinted:
        return GoalType.coerce(str(hinted)).value
    if anchors.get("goal_type"):
        return GoalType.coerce(str(anchors["goal_type"])).value
    return GoalType.GENERAL.value


def _extract_anchors_from_intent(intent: str, goal_type: str) -> dict[str, Any]:
    return {"goal_type": goal_type}


def _text_mentions_goal(text: str, goal: Goal) -> bool:
    return bool(goal.title and _fold(text) == _fold(goal.title))


async def resolve(
    session: AsyncSession,
    person_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    llm_goal: Any | None,
    user_message: str,
) -> ResolverResult:
    """
    Decide what to do with the goal signal from this turn.

    Called synchronously inside the chat path — must be fast.
    No extra LLM call is made here.
    """
    parsed = grounded_life_aim(user_message, llm_goal)
    if parsed is None:
        return ResolverResult(
            action=GoalWriteAction.NONE.value, goal=None, intelligence_enqueued=False
        )
    from pai.domains.student.person.write_lock import lock_person
    await lock_person(session, person_id)
    intent = parsed.intent
    supersedes = parsed.supersedes

    goal_type = _classify_goal_type(intent, {}, llm_goal=llm_goal)
    allowed = {"degree_level", "program", "target_country", "target_company", "role",
               "intake_year", "intake_term", "target_universities"}
    extracted = getattr(llm_goal, "anchors", None) or {}
    anchors = {k: v for k, v in extracted.items() if k in allowed and v is not None}
    anchors.update(goal_type=goal_type, title=intent[:256])
    active_goal = await get_conversation_active_goal(session, conversation_id, person_id)
    existing_id = getattr(llm_goal, "existing_goal_id", None)
    if existing_id:
        anchors["existing_goal_id"] = str(existing_id)
    existing = await find_matching_goal(session, person_id, anchors)
    if existing_id and existing is None:
        # Unknown, archived or another person's ID cannot authorize a write.
        return ResolverResult(GoalWriteAction.NONE.value, None, False)
    if existing is not None:
        anchors.pop("existing_goal_id", None)
        changed = await _update_and_maybe_enqueue(session, existing, anchors, person_id, False)
        if supersedes or active_goal is None:
            await activate_goal(session, existing, conversation_id=conversation_id)
        action = (GoalWriteAction.SWITCH if supersedes else
                  GoalWriteAction.CREATE_SECONDARY if active_goal is not None and active_goal.id != existing.id
                  else GoalWriteAction.REINFORCE)
        return ResolverResult(action.value, existing, changed or await _maybe_enqueue(session, existing))

    # 4. Create new goal
    should_activate = supersedes or active_goal is None
    goal, action = await upsert_goal_from_anchors(
        session,
        person_id,
        goal_type=goal_type,
        title=intent[:256],
        anchors=anchors,
        source_conversation_id=conversation_id,
        activate=should_activate,
        create_if_new=True,
    )
    if action == GoalWriteAction.NONE.value or goal is None:
        return ResolverResult(
            action=GoalWriteAction.NONE.value, goal=None, intelligence_enqueued=False
        )
    enqueued_job = await enqueue_goal_intelligence_job(session, goal)
    return ResolverResult(
        action=action,
        goal=goal,
        intelligence_enqueued=enqueued_job is not None,
    )


def _has_hard_conflict_on_goal(goal: Goal, anchors: dict[str, Any]) -> bool:
    from pai.domains.goals.service import _has_hard_conflict

    return _has_hard_conflict(goal, anchors)


async def _update_and_maybe_enqueue(
    session: AsyncSession,
    goal: Goal,
    anchors: dict[str, Any],
    person_id: uuid.UUID,
    activate: bool,
) -> bool:
    from pai.domains.goals.service import INTEL_STALE, update_goal_anchors

    del person_id, activate
    changed = await update_goal_anchors(session, goal, anchors)
    if changed:
        goal.intelligence_status = INTEL_STALE
        await enqueue_goal_intelligence_job(session, goal)
    return changed


async def _maybe_enqueue(session: AsyncSession, goal: Goal) -> bool:
    from pai.domains.goals.service import INTEL_PENDING, INTEL_STALE

    if goal.intelligence_status in (INTEL_PENDING, INTEL_STALE):
        job = await enqueue_goal_intelligence_job(session, goal)
        return job is not None
    return False
