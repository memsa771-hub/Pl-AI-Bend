from __future__ import annotations

from typing import Any


def normalize_test_score(value: Any, *, exam: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        out = dict(value)
        out.setdefault("exam", exam)
        return out
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"exam": exam, "overall": float(value)}
    if isinstance(value, str) and value.strip():
        try:
            return {"exam": exam, "overall": float(value.strip())}
        except ValueError:
            return {"exam": exam, "raw": value.strip()}
    return None
