from __future__ import annotations

from pai.domains.student.normalization.geo import coerce_country


def normalize_country_code(value: str | None) -> str | None:
    if not value:
        return None
    coerced = coerce_country(value)
    if isinstance(coerced, str) and coerced:
        return coerced
    return str(value).strip().upper()[:2] or None
