from typing import Literal
from pydantic import BaseModel, Field
from pai.kernel.contracts.schemas import TaskProposal

class TurnUnderstanding(BaseModel):
    turn_kind: Literal["PERSONAL_ADVICE", "PROFILE_UPDATE", "LIVE_RESEARCH", "DOCUMENT", "ACTION"] = "PERSONAL_ADVICE"
    research_state: Literal["required", "not_required", "unknown"] = "unknown"
    stance: Literal["answer", "explore", "understand", "guide"] = "answer"
    focus: str = Field(default="", max_length=600)
    intervention: Literal["answer", "explore", "clarify", "research", "ask"] = "answer"
    question_field: str | None = None
    question: str | None = Field(default=None, max_length=500)
    evidence: str = Field(default="", max_length=1000)
    task_proposals: list[TaskProposal] = Field(default_factory=list, max_length=3)
    status: Literal["ready", "unavailable"] = "ready"
