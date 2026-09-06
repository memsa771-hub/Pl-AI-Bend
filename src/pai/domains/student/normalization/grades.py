"""Lossless numeric grade parsing; parsing never establishes equivalency."""
from __future__ import annotations
import math
import re
from typing import Any

_NUMBER = r"\d+(?:[.,]\d+)?"
_GRADE = re.compile(rf"(?:GPA|CGPA)?\s*[:=]?\s*(?P<value>{_NUMBER})(?:\s*/\s*(?P<scale>{_NUMBER}))?", re.I)

def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None

def parse_grade(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        raw = value.get("value", value.get("gpa"))
        scale_raw = value.get("scale", value.get("gpa_scale", value.get("gpaScale")))
        original = value.get("original_text") or str(raw)
        context = value.get("grading_system")
        kind = value.get("type", "cumulative")
    elif isinstance(value, str):
        match = _GRADE.fullmatch(value.strip())
        if not match:
            return None
        raw, scale_raw = match.group("value", "scale")
        original, context, kind = value, None, "cumulative"
    else:
        raw, scale_raw = value, None
        original, context, kind = str(value), None, "cumulative"
    number, scale = finite_number(raw), finite_number(scale_raw)
    if number is None or number < 0:
        return None
    if scale_raw is not None and (scale is None or scale <= 0):
        return None
    return {**(value if isinstance(value, dict) else {}), "value": number, "scale": scale, "type": kind,
            "original_text": original, "grading_system": context,
            "requires_confirmation": scale is None}
