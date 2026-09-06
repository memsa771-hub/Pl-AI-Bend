from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResumeExtraction(BaseModel):
    full_name: str | None = None
    skills: list[str] = Field(default_factory=list)
    work_history: list[dict[str, Any]] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)
    evidence_text: str = ""


def to_field_map(row: ResumeExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    if row.full_name:
        out.append(("identity.full_name", row.full_name, row.evidence_text))
    if row.skills:
        out.append(("career.skills", [{"name": s} for s in row.skills], row.evidence_text))
    if row.work_history:
        out.append(("career.work_history", row.work_history, row.evidence_text))
    if row.education:
        out.append(("education.records", row.education, row.evidence_text))
    return out
