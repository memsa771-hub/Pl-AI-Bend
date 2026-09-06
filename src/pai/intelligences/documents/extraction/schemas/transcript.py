from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TranscriptExtraction(BaseModel):
    student_name: str | None = None
    institution: str | None = None
    degree: str | None = None
    program: str | None = None
    cumulative_gpa: float | None = None
    gpa_scale: float | None = 4.0
    evidence_text: str = ""
    courses: list[dict[str, Any]] = Field(default_factory=list)


def to_field_map(row: TranscriptExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    if row.student_name:
        out.append(("identity.full_name", row.student_name, row.evidence_text))
    if row.institution or row.degree or row.program:
        out.append(
            (
                "education.records",
                {
                    "institution": row.institution,
                    "degree": row.degree,
                    "program": row.program,
                },
                row.evidence_text,
            )
        )
    if row.cumulative_gpa is not None:
        out.append(
            (
                "education.gpa",
                {"value": row.cumulative_gpa, "scale": row.gpa_scale or 4.0, "type": "cumulative"},
                row.evidence_text,
            )
        )
    return out
