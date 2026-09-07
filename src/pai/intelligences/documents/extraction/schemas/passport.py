from __future__ import annotations

from pydantic import BaseModel

from .common import EvidenceValue, field_tuple


class PassportExtraction(BaseModel):
    full_name: EvidenceValue | None = None
    date_of_birth: EvidenceValue | None = None
    nationality: EvidenceValue | None = None
    passport_number: EvidenceValue | None = None
    issue_date: EvidenceValue | None = None
    expiry_date: EvidenceValue | None = None
    issuing_authority: EvidenceValue | None = None


def to_field_map(row: PassportExtraction) -> list[tuple[str, object, str]]:
    pairs = (
        field_tuple("identity.full_name", row.full_name),
        field_tuple("demographics.date_of_birth", row.date_of_birth),
        field_tuple("demographics.nationality", row.nationality),
        field_tuple("mobility.passport_number", row.passport_number),
    )
    return [item for item in pairs if item is not None]
