from __future__ import annotations

from typing import Any

from pai.intelligences.documents.config import policy
from pai.intelligences.documents.normalization.gpa import gpa_on_4, parse_gpa


def _kind(field_key: str) -> str | None:
    return (policy().get("comparators") or {}).get(field_key)


def values_equivalent(field_key: str, left: Any, right: Any) -> bool:
    if left == right:
        return True
    if _kind(field_key) == "gpa":
        a, b = parse_gpa(left), parse_gpa(right)
        if a is None or b is None:
            return False
        na, nb = gpa_on_4(a), gpa_on_4(b)
        return na is not None and nb is not None and abs(na - nb) < 0.05
    return False


def relative_delta(field_key: str, left: Any, right: Any) -> float:
    if _kind(field_key) == "gpa":
        a, b = parse_gpa(left), parse_gpa(right)
        if not a or not b:
            return 1.0
        na, nb = gpa_on_4(a), gpa_on_4(b)
        if na is None or nb is None or max(na, nb) == 0:
            return 1.0
        return abs(na - nb) / max(na, nb)
    if left == right:
        return 0.0
    return 1.0
