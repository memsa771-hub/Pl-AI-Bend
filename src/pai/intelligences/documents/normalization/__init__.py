from __future__ import annotations

from typing import Any

from pai.intelligences.documents.config import policy
from pai.intelligences.documents.normalization.dates import parse_date
from pai.intelligences.documents.normalization.gpa import parse_gpa
from pai.intelligences.documents.normalization.names import normalize_person_name
from pai.intelligences.documents.normalization.tests import normalize_test_score


def normalize_field(field_key: str, value: Any, *, document_type: str = "") -> tuple[Any, float]:
    rules = policy()
    groups = rules.get("normalizers") or {}
    ok = float(rules.get("normalize_ok") or 0.95)
    weak = float(rules.get("normalize_weak") or 0.4)
    if field_key in set(groups.get("gpa") or []):
        parsed = parse_gpa(value)
        return (parsed if parsed is not None else value, ok if parsed else weak)
    if field_key in set(groups.get("date") or []):
        parsed = parse_date(value)
        return (parsed if parsed is not None else value, ok if parsed else weak)
    if field_key in set(groups.get("name") or []) and isinstance(value, str):
        parsed = normalize_person_name(value)
        return (parsed or value, 0.9 if parsed else 0.5)
    if field_key in set(groups.get("test") or []):
        parsed = normalize_test_score(value, exam=document_type)
        return (parsed if parsed is not None else value, 0.9 if parsed else weak)
    return value, 0.8
