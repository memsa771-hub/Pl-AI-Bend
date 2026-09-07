from __future__ import annotations

from pydantic import BaseModel

from .common import EvidenceValue, field_tuple


class TestScoreExtraction(BaseModel):
    candidate_name: EvidenceValue | None = None
    exam: EvidenceValue | None = None
    test_date: EvidenceValue | None = None
    overall: EvidenceValue | None = None
    listening: EvidenceValue | None = None
    reading: EvidenceValue | None = None
    writing: EvidenceValue | None = None
    speaking: EvidenceValue | None = None


def to_field_map(row: TestScoreExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    identity = field_tuple("identity.full_name", row.candidate_name)
    if identity:
        out.append(identity)
    score_fields = [row.exam, row.test_date, row.overall, row.listening, row.reading, row.writing, row.speaking]
    payload = {
        key: item.value
        for key, item in (("exam", row.exam), ("test_date", row.test_date), ("overall", row.overall),
                          ("listening", row.listening), ("reading", row.reading),
                          ("writing", row.writing), ("speaking", row.speaking))
        if item is not None
    }
    if payload:
        out.append(("application.test_scores", payload, " | ".join(item.evidence for item in score_fields if item)))
    return out
