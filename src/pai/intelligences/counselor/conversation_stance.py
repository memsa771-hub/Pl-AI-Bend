"""Conversation stance — deterministic, per-turn counselor posture.

The counselor's problem is not knowing *what* the student wants (Vault, goals,
and discovery already track that). It is knowing *how* to respond: when to
explore, when to understand motivation, and when it is finally time to guide
toward execution.

This module answers one question with no LLM call:

    "Given this turn, what posture should the counselor take?"

It encodes the sequence from
``docs/PAI_Counselor_Conversation_Tone_Problem.md``:

    MEET THE MOMENT -> UNDERSTAND THE GOAL -> VALIDATE FIT -> GUIDE & EXECUTE

The output is advisory only: a compact ``focus`` line surfaced to the counselor
as background guidance, never a hard command. The system prompt does the heavy
lifting; this signal simply keeps the counselor from jumping to execution the
moment a goal string exists.

Pure and deterministic, mirroring ``discovery.py`` so it is easy to unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Phases, ordered from earliest to latest in the counseling sequence.
Phase = str  # "answer" | "explore" | "understand" | "guide"

# Student sounds unsure / is asking to be helped toward a direction.
_UNCERTAINTY = re.compile(
    r"\b("
    r"no idea|not sure|unsure|don'?t know|do not know|dunno|"
    r"can'?t decide|cannot decide|hard to decide|"
    r"confused|no clue|not certain|"
    r"help me (?:decide|choose|figure|pick|understand)|"
    r"where (?:should|do) i|what should i (?:do|study|choose|pick|go)|"
    r"which (?:one|country|field|degree|program|path) (?:should|is)|"
    r"i'?m lost|overwhelmed|torn between|stuck|so many options"
    r")\b",
    re.I,
)

# Student is stating / committing to a direction (confident).
_DECISION = re.compile(
    r"\b("
    r"i (?:want|plan|decided|intend|aim|hope|wish) to|"
    r"i'?ve decided|i have decided|i'?m planning|i am planning|"
    r"my (?:goal|plan|dream|aim) is|"
    r"i'?m going to|i am going to|i'?m aiming|"
    r"i'?m set on|set on|i'?ll (?:apply|study|do|go)"
    r")\b",
    re.I,
)

_FOCUS: dict[Phase, str] = {
    "answer": (
        "Answer directly and helpfully; weave in what you already know. "
        "Do not turn this into profile questions."
    ),
    "explore": (
        "They sound unsure — meet that first. Normalise it, make clear you "
        "don't need to pick anything yet, and ask what matters most to them "
        "(their priorities/values), not a profile field."
    ),
    "understand": (
        "Understand this goal before planning it. Acknowledge it warmly, then "
        "explore WHY it matters to them and whether it fits — before "
        "requirements, gaps, or next steps. Confidence is not permission to "
        "start executing."
    ),
    "guide": (
        "You understand this goal well enough — now guide toward it. Give a "
        "clear, personalised recommendation and concrete next steps, using the "
        "goal brief as background rather than reciting it."
    ),
}

# Turn kinds that should just be answered well, never redirected into
# motivation/exploration (mirrors routing.classify_turn output).
_ANSWER_TURN_KINDS = frozenset({"LIVE_RESEARCH", "DOCUMENT", "ACTION"})


@dataclass(frozen=True)
class ConversationStance:
    phase: Phase
    focus: str


def _stance(phase: Phase) -> ConversationStance:
    return ConversationStance(phase=phase, focus=_FOCUS[phase])


def compute_stance(
    *,
    message: str,
    turn_kind: str,
    is_greeting: bool,
    has_active_goal: bool,
    active_goal_status: str | None = None,
    decision_signal: bool = False,
    prior_assistant_turns: int = 0,
) -> ConversationStance:
    """Decide the counselor's posture for this turn.

    Defaults are conservative: when in doubt the stance is ``answer`` or
    ``understand`` (never a premature ``guide``), so the counselor errs toward
    understanding the person rather than executing a checklist.

    Parameters mirror signals already available in ``build_counselor_context``:
    - ``turn_kind`` / ``is_greeting`` from ``routing`` (no extra work).
    - ``active_goal_status`` is the goal-intelligence status (pending/running/
      partial/ready/failed); ``ready``/``partial`` means intelligence exists.
    - ``decision_signal`` is truthy when peer/social pressure was detected.
    - ``prior_assistant_turns`` is how many assistant replies already exist in
      this thread (the opening counts), used so brand-new goals are understood
      before they are executed.
    """
    text = message or ""

    # 1. Factual / greeting / operational turns: just answer them well.
    if is_greeting or turn_kind in _ANSWER_TURN_KINDS:
        return _stance("answer")

    uncertain = bool(_UNCERTAINTY.search(text))

    if not has_active_goal:
        if uncertain:
            return _stance("explore")
        # A fresh direction, or a profile-bearing statement, is a hypothesis to
        # understand — not yet something to plan.
        if _DECISION.search(text) or turn_kind == "PROFILE_UPDATE":
            return _stance("understand")
        return _stance("answer")

    # 2. There is an active goal.
    # New uncertainty about an existing goal pulls back to understanding.
    if uncertain:
        return _stance("understand")

    # Peer/social pressure means fit is not yet established — understand first.
    if decision_signal:
        return _stance("understand")

    # Guide only once the goal is genuinely established: intelligence has been
    # computed AND the pair has exchanged a couple of turns about it.
    intelligence_ready = (active_goal_status or "").lower() in ("ready", "partial")
    if intelligence_ready and prior_assistant_turns >= 2:
        return _stance("guide")

    return _stance("understand")
