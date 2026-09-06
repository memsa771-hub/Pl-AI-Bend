from __future__ import annotations

from pydantic import BaseModel


class DegreeExtraction(BaseModel):
    student_name: str | None = None
    institution: str | None = None
    degree: str | None = None
    program: str | None = None
    graduation_year: int | None = None
    evidence_text: str = ""


def to_field_map(row: DegreeExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    if row.student_name:
        out.append(("identity.full_name", row.student_name, row.evidence_text))
    if row.institution or row.degree:
        out.append(
            (
                "education.records",
                {
                    "institution": row.institution,
                    "degree": row.degree,
                    "program": row.program,
                    "graduationYear": row.graduation_year,
                },
                row.evidence_text,
            )
        )
    return out
