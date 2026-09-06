from __future__ import annotations
from typing import Any
from pai.intelligences.documents.config import policy
from pai.domains.student.normalization.grades import parse_grade

def _comparable(left: Any, right: Any):
    a, b = parse_grade(left), parse_grade(right)
    if not a or not b or a["scale"] is None or a["scale"] != b["scale"]:
        return None
    if not a["grading_system"] or a["grading_system"] != b["grading_system"]:
        return None
    return a, b

def values_equivalent(field_key: str, left: Any, right: Any) -> bool:
    if (policy().get("comparators") or {}).get(field_key) == "gpa":
        pair = _comparable(left, right)
        return bool(pair and pair[0]["value"] == pair[1]["value"])
    return left == right

def relative_delta(field_key: str, left: Any, right: Any) -> float:
    # Unknown grading context requires review, never a guessed conversion.
    return 0.0 if values_equivalent(field_key, left, right) else 1.0
