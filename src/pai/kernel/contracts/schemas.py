from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Explicitness = Literal["explicit", "strongly_implied", "uncertain"]
AssertionStatus = Literal[
    "explicit", "inferred", "uncertain", "negated", "hypothetical"
]
CandidateOutcome = Literal[
    "accept", "reinforce", "pending_confirmation", "conflict", "reject"
]

# Escape hatch: not a Vault catalog key. Survives extraction, never auto-writes Vault.
OBSERVED_FIELD_KEY = "memory.observed"


class VaultCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    field_key: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    explicitness: Explicitness = "explicit"
    assertion_status: AssertionStatus = "explicit"
    attributed_to: str | None = None
    fact_type: str | None = None
    source_type: Literal[
        "chat", "document", "manual", "auth", "system", "linkedin", "social"
    ] = "chat"
    source_reference: str
    evidence_text: str
    is_correction: bool = False
    requires_confirmation: bool = False
    rationale_summary: str = ""

    @field_validator("assertion_status", mode="before")
    @classmethod
    def _assertion_status_token(cls, value: object) -> object:
        if isinstance(value, str):
            token = value.strip().lower()
            aliases = {"strongly_implied": "inferred", "implied": "inferred"}
            token = aliases.get(token, token)
            allowed = {
                "explicit",
                "inferred",
                "uncertain",
                "negated",
                "hypothetical",
            }
            return token if token in allowed else "uncertain"
        return value

    @field_validator("attributed_to", mode="before")
    @classmethod
    def _attributed_to_token(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value


class CandidateResult(BaseModel):
    candidate: VaultCandidate
    outcome: CandidateOutcome
    rationale_summary: str = ""


class VaultChange(BaseModel):
    field_key: str
    status: str
    confidence: float


class PendingConfirmation(BaseModel):
    field_key: str
    value: Any
    evidence_text: str
    source_reference: str


class TaskProposal(BaseModel):
    title: str
    detail: str | None = None
    requires_confirmation: bool = False
    kind: Literal["student_action", "profile_write"] = "student_action"


class TaskResult(BaseModel):
    title: str
    status: str
    task_id: str | None = None
    detail: str | None = None


class ConversationResult(BaseModel):
    reply: str
    known_facts_used: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    suggested_next_step: str | None = None
    next_question: str | None = None
    task_proposals: list[TaskProposal] = Field(default_factory=list)


class GoalExtract(BaseModel):
    """Living brief in the student's words — language-agnostic, not an enum."""

    kind: Literal["life_aim", "turn_action", "none"] = Field(
        default="none",
        description=(
            "life_aim = durable first-person life direction in any language; "
            "turn_action = this-chat task or a request to the counselor; "
            "none = greeting, question, or no new direction"
        ),
    )
    stated: bool = False
    intent: str | None = None
    mode: Literal["pursuing", "exploring"] | None = None
    supersedes_previous: bool = False
    evidence_text: str | None = None
    goal_type: Literal["admission", "job", "internship", "general"] | None = Field(
        default=None,
        description="LLM classification of the life aim. Python validates against GoalType.",
    )

    @field_validator("goal_type", mode="before")
    @classmethod
    def _goal_type_token(cls, value: object) -> object:
        if value in (None, ""):
            return None
        from pai.domains.goals.types import GoalType

        return GoalType.coerce(str(value)).value


class FactExtractionResult(BaseModel):
    fact_candidates: list[VaultCandidate] = Field(default_factory=list)
    current_goal: GoalExtract | None = None


class RunError(BaseModel):
    code: str
    message: str
    step: str | None = None
