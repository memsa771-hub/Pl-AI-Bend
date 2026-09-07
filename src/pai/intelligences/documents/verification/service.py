from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.kernel.errors import AuthError
from pai.kernel.gates import accept_vault_candidates
from pai.kernel.contracts.schemas import VaultCandidate
from pai.intelligences.documents.config import policy
from pai.domains.documents.models import Document, DocumentFact, VerificationCase
from pai.domains.student.person.models import Person

RESOLUTIONS = {
    "resolved_document_correct",
    "resolved_existing_correct",
    "resolved_wrong_document",
    "resolved_different_entity",
    "resolved_custom",
    "dismissed",
}


class CaseNotFoundError(AuthError):
    def __init__(self) -> None:
        super().__init__(code="VERIFICATION_NOT_FOUND", message="Verification case not found.", status_code=404)


async def open_case(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    document_id: uuid.UUID | None,
    fact: DocumentFact | None,
    field_key: str,
    existing_value,
    incoming_value,
    case_type: str,
    reason_code: str,
    severity: str = "high",
) -> VerificationCase:
    existing = await session.scalar(
        select(VerificationCase).where(
            VerificationCase.person_id == person_id,
            VerificationCase.document_id == document_id,
            VerificationCase.field_key == field_key,
            VerificationCase.case_type == case_type,
            VerificationCase.status.in_(("open", "presented")),
        )
    )
    if existing is not None:
        existing.incoming_document_fact_id = fact.id if fact is not None else None
        existing.existing_value = existing_value
        existing.incoming_value = incoming_value
        existing.reason_code = reason_code
        existing.severity = severity
        existing.status = "open"
        return existing
    row = VerificationCase(
        person_id=person_id,
        document_id=document_id,
        incoming_document_fact_id=fact.id if fact is not None else None,
        case_type=case_type,
        severity=severity,
        field_key=field_key,
        existing_value=existing_value,
        incoming_value=incoming_value,
        reason_code=reason_code,
        status="open",
    )
    session.add(row)
    return row


async def list_open_cases(session: AsyncSession, person_id: uuid.UUID) -> list[VerificationCase]:
    result = await session.execute(
        select(VerificationCase)
        .where(
            VerificationCase.person_id == person_id,
            VerificationCase.status.in_(("open", "presented")),
        )
        .order_by(VerificationCase.created_at.desc())
    )
    return list(result.scalars().all())


async def close_open_cases_for_fields(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    document_id: uuid.UUID,
    field_keys: set[str],
    resolution_type: str = "resolved_document_correct",
    notes: str | None = None,
) -> None:
    """Close matching cases after candidate review already applied Vault facts."""
    if not field_keys:
        return
    result = await session.execute(
        select(VerificationCase).where(
            VerificationCase.person_id == person_id,
            VerificationCase.document_id == document_id,
            VerificationCase.status.in_(("open", "presented")),
            VerificationCase.field_key.in_(tuple(field_keys)),
        )
    )
    now = datetime.now(UTC)
    for case in result.scalars():
        case.status = resolution_type
        case.resolution_type = resolution_type
        case.resolution_notes = notes or "closed_via_candidate_review"
        case.resolved_at = now
        if case.incoming_document_fact_id:
            fact = await session.get(DocumentFact, case.incoming_document_fact_id)
            if fact is not None:
                fact.reconciliation_status = "applied_user_confirmed"


async def resolve_case(
    session: AsyncSession,
    person: Person,
    case_id: uuid.UUID,
    *,
    resolution_type: str,
    notes: str | None = None,
) -> VerificationCase:
    if resolution_type not in RESOLUTIONS:
        raise AuthError(code="INVALID_RESOLUTION", message="Unknown resolution type.", status_code=400)
    from pai.domains.student.person.write_lock import lock_person
    await lock_person(session, person.id)
    result = await session.execute(
        select(VerificationCase).where(
            VerificationCase.id == case_id,
            VerificationCase.person_id == person.id,
        ).with_for_update()
    )
    case = result.scalar_one_or_none()
    if case is None:
        raise CaseNotFoundError()
    if case.resolved_at is not None:
        if case.resolution_type == resolution_type:
            return case
        raise AuthError("RESOLUTION_CONFLICT", "This case was already resolved.", 409)
    case.status = resolution_type
    case.resolution_type = resolution_type
    case.resolution_notes = notes
    case.resolved_at = datetime.now(UTC)
    if resolution_type == "resolved_document_correct" and case.incoming_document_fact_id:
        fact = await session.get(DocumentFact, case.incoming_document_fact_id)
        if fact is None or fact.person_id != person.id or not (fact.evidence_text or "").strip():
            await session.rollback()
            raise AuthError("RESOLUTION_NOT_APPLIED", "The document evidence is unavailable.", 422)
        if fact is not None:
            outcomes, pending = await accept_vault_candidates(
                session,
                person,
                [
                    VaultCandidate(
                        field_key=fact.field_key,
                        value=fact.normalized_value,
                        confidence=fact.extraction_confidence,
                        evidence_text=fact.evidence_text or "",
                        source_type="document",
                        source_reference=str(fact.document_id),
                        rationale_summary=f"user_resolution:{resolution_type}",
                    )
                ],
                from_document=True,
                already_reconciled=True,
                apply_order=list(policy().get("apply_order") or []),
            )
            if not outcomes or pending or any(o.status not in ("accepted", "reinforced", "updated") for o in outcomes):
                await session.rollback()
                raise AuthError("RESOLUTION_NOT_APPLIED", "The fact needs further review.", 422)
            fact.reconciliation_status = "applied_user_confirmed"
    if resolution_type == "resolved_wrong_document" and case.document_id:
        doc = await session.get(Document, case.document_id)
        if doc is not None:
            doc.identity_status = "mismatch"
            doc.verification_status = "identity_mismatch"
    await session.commit()
    return case


def public_case(row: VerificationCase) -> dict:
    return {
        "id": str(row.id),
        "caseType": row.case_type,
        "severity": row.severity,
        "fieldKey": row.field_key,
        "existingValue": row.existing_value,
        "incomingValue": row.incoming_value,
        "reasonCode": row.reason_code,
        "status": row.status,
        "documentId": str(row.document_id) if row.document_id else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }
