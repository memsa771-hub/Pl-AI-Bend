from __future__ import annotations

from pydantic import BaseModel


class TestScoreExtraction(BaseModel):
    candidate_name: str | None = None
    exam: str | None = None
    test_date: str | None = None
    overall: float | None = None
    listening: float | None = None
    reading: float | None = None
    writing: float | None = None
    speaking: float | None = None
    evidence_text: str = ""


def to_field_map(row: TestScoreExtraction) -> list[tuple[str, object, str]]:
    out: list[tuple[str, object, str]] = []
    if row.candidate_name:
        out.append(("identity.full_name", row.candidate_name, row.evidence_text))
    payload = row.model_dump(exclude={"evidence_text", "candidate_name"}, exclude_none=True)
    if payload:
        out.append(("application.test_scores", payload, row.evidence_text))
    return out
