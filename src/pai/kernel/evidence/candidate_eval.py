from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.kernel.contracts.schemas import CandidateResult, VaultCandidate
from pai.kernel.policy.verifier import policy_decision, validate_candidate
from pai.kernel.evidence.assertion import assertion_of, is_vault_eligible
from pai.domains.student.person.models import Person, VaultValue
from pai.domains.student.vault.catalog import get_catalog_field


def _normalize(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value).strip().lower()


async def load_candidate_validation_context(
    session: AsyncSession,
    person: Person,
) -> dict[str, Any]:
    """Load vault state once for evaluating many candidates in a turn."""
    active_values: dict[str, Any] = {}
    if person.vault is not None:
        result = await session.execute(
            select(VaultValue.field_key, VaultValue.value).where(
                VaultValue.vault_id == person.vault.id,
                VaultValue.status == "active",
            )
        )
        for field_key, value in result.all():
            active_values[field_key] = value
    return {"active_values": active_values}


def evaluate_candidate(
    candidate: VaultCandidate,
    existing_state: dict[str, Any],
) -> CandidateResult:
    """Pure evaluation against a preloaded vault snapshot."""
    if not is_vault_eligible(candidate):
        return CandidateResult(
            candidate=candidate,
            outcome="reject",
            rationale_summary="Not student vault truth (observed / attributed / non-asserted)",
        )
    validated = validate_candidate(candidate)
    if validated is None:
        return CandidateResult(
            candidate=candidate,
            outcome="reject",
            rationale_summary="Failed schema or catalog validation",
        )
    field = get_catalog_field(candidate.field_key)
    assert field is not None
    active_values = existing_state.get("active_values") or {}
    existing_val = active_values.get(candidate.field_key)

    if existing_val is not None and _normalize(existing_val) == _normalize(candidate.value):
        return CandidateResult(
            candidate=candidate,
            outcome="reinforce",
            rationale_summary="Same normalized value already active",
        )
    if existing_val is not None and _normalize(existing_val) != _normalize(candidate.value):
        if candidate.is_correction and assertion_of(candidate) == "explicit":
            pass
        else:
            return CandidateResult(
                candidate=candidate,
                outcome="conflict",
                rationale_summary="Different active value exists",
            )

    if candidate.requires_confirmation or field.sensitive:
        if (
            candidate.explicitness == "explicit"
            and assertion_of(candidate) == "explicit"
            and not field.sensitive
        ):
            decision = policy_decision(validated)
        else:
            return CandidateResult(
                candidate=candidate,
                outcome="pending_confirmation",
                rationale_summary="Sensitive or requires confirmation",
            )
    else:
        decision = policy_decision(validated)

    if decision == "reject":
        return CandidateResult(
            candidate=candidate,
            outcome="reject",
            rationale_summary="Policy rejected candidate",
        )
    if decision == "pending":
        return CandidateResult(
            candidate=candidate,
            outcome="pending_confirmation",
            rationale_summary="Pending confirmation per policy",
        )
    return CandidateResult(
        candidate=candidate,
        outcome="accept",
        rationale_summary="Explicit valid non-sensitive fact",
    )


async def evaluate_candidate_with_context(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    *,
    existing_state: dict[str, Any] | None = None,
) -> CandidateResult:
    state = existing_state or await load_candidate_validation_context(session, person)
    return evaluate_candidate(candidate, state)


async def evaluate_candidates_batch(
    session: AsyncSession,
    person: Person,
    candidates: list[VaultCandidate],
) -> list[CandidateResult]:
    if not candidates:
        return []
    existing_state = await load_candidate_validation_context(session, person)
    return [evaluate_candidate(c, existing_state) for c in candidates]
