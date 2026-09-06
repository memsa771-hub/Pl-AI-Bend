from __future__ import annotations

from pai.intelligences.documents.config import policy


_RANK_FALLBACK = {"low": 0, "normal": 1, "high": 2, "critical": 3}


def field_criticality(field_key: str) -> str:
    return str((policy().get("field_criticality") or {}).get(field_key) or "normal")


def field_sensitivity(field_key: str) -> str:
    return str((policy().get("field_sensitivity") or {}).get(field_key) or "personal")


def rank(level: str) -> int:
    ranks = policy().get("criticality_rank") or _RANK_FALLBACK
    return int(ranks.get(level, 1))
