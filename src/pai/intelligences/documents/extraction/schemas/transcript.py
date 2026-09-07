from __future__ import annotations

from pydantic import BaseModel

from .common import EvidenceValue, GradeEvidence, field_tuple


class TranscriptExtraction(BaseModel):
    student_name: EvidenceValue | None = None
    institution: EvidenceValue | None = None
    degree: EvidenceValue | None = None
    program: EvidenceValue | None = None
    cumulative_gpa: GradeEvidence | None = None
    courses: EvidenceValue | None = None


def to_field_map(row: TranscriptExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    identity = field_tuple("identity.full_name", row.student_name)
    if identity:
        out.append(identity)
    if row.institution or row.degree or row.program:
        fields = [item for item in (row.institution, row.degree, row.program) if item is not None]
        out.append(
            (
                "education.records",
                {
                    "institution": row.institution.value if row.institution else None,
                    "degree": row.degree.value if row.degree else None,
                    "program": row.program.value if row.program else None,
                },
                " | ".join(item.evidence for item in fields),
            )
        )
    if row.cumulative_gpa is not None:
        out.append(
            (
                "education.gpa",
                {"value": row.cumulative_gpa.value, "scale": row.cumulative_gpa.scale, "type": "cumulative"},
                row.cumulative_gpa.evidence,
            )
        )
    return out
