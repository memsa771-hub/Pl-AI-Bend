from __future__ import annotations

from pydantic import BaseModel

from .common import EvidenceValue, field_tuple


class ResumeExtraction(BaseModel):
    full_name: EvidenceValue | None = None
    skills: EvidenceValue | None = None
    work_history: EvidenceValue | None = None
    education: EvidenceValue | None = None


def to_field_map(row: ResumeExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    identity = field_tuple("identity.full_name", row.full_name)
    if identity:
        out.append(identity)
    if row.skills:
        out.append(("career.skills", [{"name": s} for s in row.skills.value], row.skills.evidence))
    if row.work_history:
        out.append(("career.work_history", row.work_history.value, row.work_history.evidence))
    if row.education:
        out.append(("education.records", row.education.value, row.education.evidence))
    return out
