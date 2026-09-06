from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import get_settings
from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.policy.verifier import (
    policy_decision,
    validate_candidate,
    verification_level_for,
)
from pai.domains.student.typed_apply import apply_typed_candidate
from pai.domains.student.person.models import Person, VaultEvidence, VaultHistory, VaultValue
from pai.domains.student.vault.catalog import get_catalog_field
from pai.domains.student.vault.completion import apply_completion_to_vault
from pai.domains.student.vault.security import SensitiveValueCodec
from pai.domains.student.vault.service import _history_value


class VaultApplyResult(BaseModel):
    field_key: str
    status: str
    confidence: float


async def apply_vault_candidate(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    *,
    vault_status: str,
    verification_level: str,
    recompute_completion: bool = True,
) -> VaultApplyResult:
    from pai.kernel.errors import UnknownFieldError

    field = get_catalog_field(candidate.field_key)
    if field is None or person.vault is None:
        raise UnknownFieldError()
    settings = get_settings()
    codec = SensitiveValueCodec(settings.vault_encryption_key)
    vault = person.vault
    result = await session.execute(
        select(VaultValue).where(
            VaultValue.vault_id == vault.id,
            VaultValue.field_key == candidate.field_key,
            VaultValue.status == "active",
        )
    )
    existing = result.scalar_one_or_none()
    if existing and existing.value == candidate.value and vault_status == "active":
        session.add(
            VaultEvidence(
                vault_value_id=existing.id,
                source_type=candidate.source_type,
                source_reference=candidate.source_reference,
                evidence_text=candidate.evidence_text,
                confidence=candidate.confidence,
            )
        )
        return VaultApplyResult(
            field_key=candidate.field_key, status="reinforced", confidence=candidate.confidence
        )
    old_val = existing.value if existing else None
    if existing:
        existing.status = "superseded"
    status = "pending_confirmation" if vault_status == "pending" else "active"
    row = VaultValue(
        vault_id=vault.id,
        field_key=candidate.field_key,
        value=None if field.sensitive else candidate.value,
        value_encrypted=codec.encrypt_json(candidate.value) if field.sensitive else None,
        status=status,
        verification_level=verification_level,
        confidence=candidate.confidence,
        supersedes_id=existing.id if existing else None,
    )
    session.add(row)
    await session.flush()
    session.add(
        VaultEvidence(
            vault_value_id=row.id,
            source_type=candidate.source_type,
            source_reference=candidate.source_reference,
            evidence_text=candidate.evidence_text,
            confidence=candidate.confidence,
        )
    )
    session.add(
        VaultHistory(
            vault_id=vault.id,
            field_key=candidate.field_key,
            action="updated" if old_val is not None else "created",
            old_value=_history_value(field, old_val),
            new_value=_history_value(field, candidate.value),
            actor_type="system",
            actor_id=str(person.id),
            reason=candidate.rationale_summary,
        )
    )
    if recompute_completion and person.vault is not None:
        await apply_completion_to_vault(session, person, person.vault)
    out_status = "pending" if status == "pending_confirmation" else "accepted"
    return VaultApplyResult(
        field_key=candidate.field_key, status=out_status, confidence=candidate.confidence
    )


async def process_candidates(
    session: AsyncSession,
    person: Person,
    candidates: list[VaultCandidate],
    *,
    from_document: bool = False,
    already_reconciled: bool = False,
    apply_order: list[str] | None = None,
) -> tuple[list[VaultApplyResult], list[VaultCandidate]]:
    accepted: list[VaultApplyResult] = []
    pending: list[VaultCandidate] = []
    mutated = False
    ordered = candidates
    if apply_order:
        order = {key: idx for idx, key in enumerate(apply_order)}
        ordered = sorted(candidates, key=lambda c: order.get(c.field_key, len(order)))
    for raw in ordered:
        candidate = validate_candidate(raw)
        if candidate is None:
            continue
        field = get_catalog_field(candidate.field_key)
        if field is None:
            continue
        decision = "accept" if already_reconciled else policy_decision(candidate, from_document=from_document)
        if decision == "reject":
            continue
        vlevel = verification_level_for(
            candidate, accepted=(decision == "accept"), from_document=from_document
        )
        vault_status = "pending" if decision == "pending" else "active"
        if decision == "pending":
            pending.append(candidate)

        if field.storage == "vault_value":
            if vault_status == "pending":
                await apply_vault_candidate(
                    session,
                    person,
                    candidate,
                    vault_status="pending",
                    verification_level=vlevel,
                    recompute_completion=False,
                )
                accepted.append(
                    VaultApplyResult(
                        field_key=candidate.field_key,
                        status="pending",
                        confidence=candidate.confidence,
                    )
                )
            else:
                accepted.append(
                    await apply_vault_candidate(
                        session,
                        person,
                        candidate,
                        vault_status="active",
                        verification_level=vlevel,
                        recompute_completion=False,
                    )
                )
            mutated = True
            continue

        typed = await apply_typed_candidate(
            session,
            person,
            candidate,
            field,
            vault_status=vault_status,
            recompute_completion=False,
        )
        if typed.status != "rejected":
            accepted.append(
                VaultApplyResult(
                    field_key=typed.field_key,
                    status=typed.status,
                    confidence=typed.confidence,
                )
            )
            mutated = True
    if mutated and person.vault is not None:
        await apply_completion_to_vault(session, person, person.vault)
    if mutated:
        from pai.domains.journey.service import record_vault_applied

        applied_keys = [row.field_key for row in accepted if row.status != "pending"]
        record_vault_applied(session, person.id, applied_keys)

        # Selective goal refresh: re-queue assessment for goals affected by these fields.
        # Include pending typed writes too — work/projects/certs are stored even when
        # pending, and gaps must refresh when the profile changes.
        refresh_keys = list(
            dict.fromkeys(
                [
                    *[row.field_key for row in accepted],
                ]
            )
        )
        try:
            from pai.domains.goals.service import mark_intelligence_stale_for_vault_update

            for fk in refresh_keys:
                await mark_intelligence_stale_for_vault_update(session, person.id, fk)
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "Goal selective refresh failed (non-fatal) for person=%s", person.id
            )
    return accepted, pending
