"""Entity-level evidence and verification.

`VaultValue` has always carried provenance; typed entities did not, so a GPA on
an education row was indistinguishable whether it came from a passing remark or
a transcript. This module gives typed entities the same support through the
generic target on `VaultEvidence` (doc §23), which is what lets reconciliation
prefer verified evidence over a casual restatement (doc §13, §28).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.student.person.models import VaultEvidence
from pai.kernel.contracts.schemas import VaultCandidate

# Ascending strength. A claim may overwrite an attribute only when its evidence
# is at least as strong as the evidence already behind that attribute.
VERIFICATION_RANK: dict[str, int] = {
    "self_reported": 0,
    "auth_verified": 1,
    "provider_verified": 2,
}
DEFAULT_VERIFICATION = "self_reported"


def verification_rank(level: str | None) -> int:
    return VERIFICATION_RANK.get(level or DEFAULT_VERIFICATION, 0)


def record_entity_evidence(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    attribute: str,
    candidate: VaultCandidate,
    verification_level: str = DEFAULT_VERIFICATION,
) -> None:
    """Attach one piece of provenance to a typed entity attribute."""
    session.add(
        VaultEvidence(
            entity_type=entity_type,
            entity_id=entity_id,
            attribute=attribute,
            source_type=candidate.source_type,
            source_reference=candidate.source_reference,
            evidence_text=candidate.evidence_text,
            confidence=candidate.confidence,
            verification_level=verification_level,
        )
    )


async def attribute_verification(
    session: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
    attributes: list[str],
) -> dict[str, str]:
    """Strongest verification level recorded per attribute of one entity."""
    if not attributes:
        return {}
    rows = (
        await session.execute(
            select(VaultEvidence.attribute, VaultEvidence.verification_level).where(
                VaultEvidence.entity_type == entity_type,
                VaultEvidence.entity_id == entity_id,
                VaultEvidence.attribute.in_(tuple(attributes)),
            )
        )
    ).all()
    strongest: dict[str, str] = {}
    for attribute, level in rows:
        if attribute is None:
            continue
        if verification_rank(level) >= verification_rank(strongest.get(attribute)):
            strongest[attribute] = level or DEFAULT_VERIFICATION
    return strongest


async def list_entity_evidence(
    session: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(VaultEvidence)
            .where(
                VaultEvidence.entity_type == entity_type,
                VaultEvidence.entity_id == entity_id,
            )
            .order_by(VaultEvidence.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "attribute": row.attribute,
            "sourceType": row.source_type,
            "sourceReference": row.source_reference,
            "evidenceText": row.evidence_text,
            "confidence": row.confidence,
            "verificationLevel": row.verification_level,
            "recordedAt": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
