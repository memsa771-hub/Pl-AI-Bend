from __future__ import annotations

import re
from typing import Any

_GPA = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:/\s*(?P<scale>\d+(?:\.\d+)?))?",
)


def parse_gpa(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        raw = value.get("value", value.get("gpa"))
        scale = value.get("scale") or value.get("gpaScale")
        kind = value.get("type") or value.get("kind") or "cumulative"
        parsed = _as_float(raw)
        if parsed is None:
            return None
        return {
            "value": parsed,
            "scale": _as_float(scale) or 4.0,
            "type": kind,
        }
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"value": float(value), "scale": 4.0, "type": "cumulative"}
    if isinstance(value, str):
        match = _GPA.search(value.replace(",", "."))
        if not match:
            return None
        return {
            "value": float(match.group("value")),
            "scale": float(match.group("scale") or 4),
            "type": "cumulative",
        }
    return None


def gpa_on_4(parsed: dict[str, Any]) -> float | None:
    value = _as_float(parsed.get("value"))
    scale = _as_float(parsed.get("scale")) or 4.0
    if value is None or scale <= 0:
        return None
    return value * (4.0 / scale)


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None
