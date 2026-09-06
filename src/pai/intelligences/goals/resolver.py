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
from pai.domains.student.normalization.geo import extract_countries_from_text

_LIFE_AIM = "life_aim"

# Phrases that mean "continue this pursuit", not "mint a new goal"
_FOCUS_PREFIX = re.compile(
    r"^(?:i\s+want\s+to\s+)?(?:focus\s+on|getting\s+into|get\s+into|apply\s+to|"
    r"applying\s+to|aiming\s+(?:for|to)|looking\s+at)\s+",
    re.IGNORECASE,
)


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
    """Evidence must be a span of the student message, not a model rewrite."""
    ev = _fold(span)
    src = _fold(source)
    if len(ev) < 4 or not src:
        return False
    if ev in src:
        return True
    tokens = [tok for tok in re.findall(r"\w+", ev, flags=re.UNICODE) if len(tok) >= 2]
    if len(tokens) < 2:
        return False
    pos = 0
    for tok in tokens:
        found = src.find(tok, pos)
        if found < 0:
            return False
        pos = found + len(tok)
    return True


def grounded_life_aim(text: str, llm_goal: Any | None) -> GroundedLifeAim | None:
    """LLM classified life_aim only if evidence is a span of the student text."""
    if llm_goal is None:
        return None
    if (getattr(llm_goal, "kind", None) or "none") != _LIFE_AIM:
        return None
    evidence = (getattr(llm_goal, "evidence_text", None) or "").strip()
    intent = (getattr(llm_goal, "intent", None) or "").strip() or evidence
    if len(intent) < 4:
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
    lower = intent.casefold()
    if any(kw in lower for kw in ("phd", "mba", "masters", "university", "admission", "ms ", "msc", "bachelor")):
        return GoalType.ADMISSION.value
    if "internship" in lower or "intern" in lower:
        return GoalType.INTERNSHIP.value
    if any(kw in lower for kw in ("job", "full-time", "full time")):
        return GoalType.JOB.value
    return GoalType.GENERAL.value


def _extract_anchors_from_intent(intent: str, goal_type: str) -> dict[str, Any]:
    """Normalize anchors from intent. Countries via student geo, not a handwritten list."""
    anchors: dict[str, Any] = {"goal_type": goal_type}
    lower = intent.casefold()
    countries = extract_countries_from_text(intent)
    if countries:
        anchors["target_country"] = countries[0]

    if goal_type == GoalType.ADMISSION:
        if re.search(r"\bphd\b", lower):
            anchors["degree_level"] = "phd"
        elif re.search(r"\bms\b|m\.s|msc|masters?\b", lower):
            anchors["degree_level"] = "ms"
        elif re.search(r"\bbs\b|b\.s|bachelor", lower):
            anchors["degree_level"] = "bs"
        elif re.search(r"\bmba\b", lower):
            anchors["degree_level"] = "mba"
    return anchors


def _goal_name_tokens(goal: Goal) -> list[str]:
    """Tokens that identify this goal for containment matching."""
    tokens: list[str] = []
    title = (goal.title or "").casefold().strip()
    if title:
        tokens.append(title)
        # Significant words from title (drop stopwords)
        for word in re.findall(r"[a-z0-9]{3,}", title):
            if word not in {"the", "and", "for", "into", "get", "getting", "want", "admission"}:
                tokens.append(word)
    for uni in (goal.anchors or {}).get("target_universities") or []:
        if isinstance(uni, str) and uni.strip():
            tokens.append(uni.casefold().strip())
    if goal.target_country:
        tokens.append(str(goal.target_country).casefold())
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen and len(t) >= 3:
            seen.add(t)
            out.append(t)
    return out


def _text_mentions_goal(text: str, goal: Goal) -> bool:
    """True if intent/message clearly refers to this existing goal (uni/title)."""
    hay = (text or "").casefold()
    if not hay:
        return False
    title = (goal.title or "").casefold().strip()
    if title and (title in hay or hay in title):
        return True
    # Strip focus-style prefixes then compare remainder to title
    stripped = _FOCUS_PREFIX.sub("", hay).strip(" .")
    if title and stripped and (stripped in title or title in stripped):
        return True
    for uni in (goal.anchors or {}).get("target_universities") or []:
        if isinstance(uni, str) and uni.casefold().strip() and uni.casefold() in hay:
            return True
    # Distinctive multi-word / proper-name tokens from title (e.g. bologna, hust, tum)
    for token in _goal_name_tokens(goal):
        if len(token) >= 4 and token in hay:
            return True
    return False


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
    intent = parsed.intent
    supersedes = parsed.supersedes

    match_text = f"{intent} {user_message or ''}"
    goal_type = _classify_goal_type(intent, {}, llm_goal=llm_goal)
    anchors = _extract_anchors_from_intent(intent, goal_type)
    anchors["title"] = intent[:256]

    active_goal = await get_conversation_active_goal(session, conversation_id, person_id)

    # 0. University / title containment against active goal (prevents "focus on Bologna" dupes)
    if active_goal is not None and not supersedes and _text_mentions_goal(match_text, active_goal):
        await activate_goal(session, active_goal, conversation_id=conversation_id)
        changed = await _update_and_maybe_enqueue(
            session, active_goal, anchors, person_id, activate=False
        )
        return ResolverResult(
            action=GoalWriteAction.REINFORCE.value,
            goal=active_goal,
            intelligence_enqueued=changed,
        )

    # 1. Compatibility against the active goal — reinforce unless anchors truly
    #    conflict. Missing anchors on the incoming side are never a mismatch,
    #    so a vague rephrase of the same pursuit cannot spawn a duplicate row.
    if active_goal is not None and not supersedes:
        full_anchors = {"goal_type": goal_type, **anchors}
        if not _has_hard_conflict_on_goal(active_goal, full_anchors):
            changed = await _update_and_maybe_enqueue(
                session, active_goal, anchors, person_id, activate=False
            )
            return ResolverResult(
                action=GoalWriteAction.REINFORCE.value,
                goal=active_goal,
                intelligence_enqueued=changed,
            )

    # 2. Containment against any existing non-archived goal
    all_goals = await list_goals(session, person_id, include_archived=False)
    mentioned = next((g for g in all_goals if _text_mentions_goal(match_text, g)), None)
    if mentioned is not None and not supersedes:
        if active_goal is None or mentioned.id == active_goal.id:
            await activate_goal(session, mentioned, conversation_id=conversation_id)
            enqueued = await _maybe_enqueue(session, mentioned)
            return ResolverResult(
                action=GoalWriteAction.REINFORCE.value,
                goal=mentioned,
                intelligence_enqueued=enqueued,
            )
        # Mentioned a different existing goal → only an explicit pivot (LLM-detected
        # supersedes_previous) switches the active goal; otherwise it's a secondary
        # pursuit and the current active goal must stay untouched.
        if supersedes:
            await activate_goal(session, mentioned, conversation_id=conversation_id)
            enqueued = await _maybe_enqueue(session, mentioned)
            return ResolverResult(
                action=GoalWriteAction.SWITCH.value,
                goal=mentioned,
                intelligence_enqueued=enqueued,
            )
        enqueued = await _maybe_enqueue(session, mentioned)
        return ResolverResult(
            action=GoalWriteAction.CREATE_SECONDARY.value,
            goal=mentioned,
            intelligence_enqueued=enqueued,
        )

    # 3. Anchor match against other goals
    full_anchors = {"goal_type": goal_type, **anchors}
    secondary = await find_matching_goal(session, person_id, full_anchors)
    if secondary is not None and (active_goal is None or secondary.id != active_goal.id):
        if supersedes:
            await activate_goal(session, secondary, conversation_id=conversation_id)
            enqueued = await _maybe_enqueue(session, secondary)
            return ResolverResult(
                action=GoalWriteAction.SWITCH.value,
                goal=secondary,
                intelligence_enqueued=enqueued,
            )
        enqueued = await _maybe_enqueue(session, secondary)
        return ResolverResult(
            action=GoalWriteAction.CREATE_SECONDARY.value,
            goal=secondary,
            intelligence_enqueued=enqueued,
        )

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
    enqueued_job = await enqueue_goal_intelligence_job(session, goal)
    if action == GoalWriteAction.NONE.value or goal is None:
        return ResolverResult(
            action=GoalWriteAction.NONE.value, goal=None, intelligence_enqueued=False
        )
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
