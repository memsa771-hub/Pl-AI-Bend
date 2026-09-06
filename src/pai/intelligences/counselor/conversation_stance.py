"""Neutral deterministic fallback; semantic posture belongs to understanding."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ConversationStance:
    phase: str
    focus: str

def compute_stance(*, message: str, turn_kind: str, is_greeting: bool,
                   has_active_goal: bool, active_goal_status=None,
                   decision_signal=False, prior_assistant_turns=0) -> ConversationStance:
    return ConversationStance("answer", "Respond to the student's current need in their language. "
        "Do not infer commitment, pressure or readiness from profile completeness or turn count.")
