from __future__ import annotations

from typing import Any, TypedDict

from pai.intelligences.counselor.context import StudentContextPack
from pai.kernel.contracts.schemas import (
    CandidateResult,
    ConversationResult,
    PendingConfirmation,
    RunError,
    TaskProposal,
    TaskResult,
    VaultCandidate,
    VaultChange,
)


class PAIState(TypedDict, total=False):
    person_id: str
    conversation_id: str
    user_message_id: str
    user_message: str

    student_context: StudentContextPack | None
    student_context_json: str
    extraction_required: bool

    fact_candidates: list[VaultCandidate]
    observed_candidates: list[VaultCandidate]
    candidate_results: list[CandidateResult]
    applied_vault_changes: list[VaultChange]
    pending_confirmations: list[PendingConfirmation]

    task_proposals: list[TaskProposal]
    task_results: list[TaskResult]

    assistant_result: ConversationResult | None
    assistant_reply: str
    assistant_message_id: str | None

    run_id: str
    run_status: str
    errors: list[RunError]

    orchestration_llm_calls: int
    semantic_memory_context: str
    tool_trace: list[dict[str, Any]]
    goal_updated: bool
    attachment_note: str
    discovery_top_field: str | None
    _session_bound: Any
