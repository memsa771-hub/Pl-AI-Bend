from __future__ import annotations

from pydantic import BaseModel, Field


class PassportExtraction(BaseModel):
    full_name: str | None = None
    date_of_birth: str | None = None
    nationality: str | None = None
    passport_number: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    issuing_authority: str | None = None
    evidence_text: str = Field(default="", min_length=0)


def to_field_map(row: PassportExtraction) -> list[tuple[str, object, str]]:
    pairs = [
        ("identity.full_name", row.full_name),
        ("demographics.date_of_birth", row.date_of_birth),
        ("demographics.nationality", row.nationality),
        ("mobility.passport_number", row.passport_number),
    ]
    return [(key, value, row.evidence_text) for key, value in pairs if value not in (None, "")]
