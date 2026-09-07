from __future__ import annotations

from pydantic import BaseModel

from .common import EvidenceValue, field_tuple


class DegreeExtraction(BaseModel):
    student_name: EvidenceValue | None = None
    institution: EvidenceValue | None = None
    degree: EvidenceValue | None = None
    program: EvidenceValue | None = None
    graduation_year: EvidenceValue | None = None


def to_field_map(row: DegreeExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    identity = field_tuple("identity.full_name", row.student_name)
    if identity:
        out.append(identity)
    if row.institution or row.degree:
        fields = [item for item in (row.institution, row.degree, row.program, row.graduation_year) if item]
        out.append(
            (
                "education.records",
                {
                    "institution": row.institution.value if row.institution else None,
                    "degree": row.degree.value if row.degree else None,
                    "program": row.program.value if row.program else None,
                    "graduationYear": row.graduation_year.value if row.graduation_year else None,
                },
                " | ".join(item.evidence for item in fields),
            )
        )
    return out
