from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EvidenceValue(BaseModel):
    value: Any
    evidence: str
    page: int | None = None


class GradeEvidence(BaseModel):
    value: float
    scale: float | None = None
    evidence: str
    page: int | None = None


def field_tuple(field_key: str, item: EvidenceValue | None):
    if item is None or item.value in (None, "", []):
        return None
    return field_key, item.value, item.evidence
