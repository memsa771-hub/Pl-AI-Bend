from __future__ import annotations

import re
import unicodedata

_SPLIT = re.compile(r"[^a-z0-9]+")


def fold_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    ascii_only = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(part for part in _SPLIT.split(ascii_only.lower()) if part)


def name_tokens(value: str | None) -> set[str]:
    return {tok for tok in fold_name(value).split() if len(tok) > 1}


def names_match(left: str | None, right: str | None) -> str:
    a, b = name_tokens(left), name_tokens(right)
    if not a or not b:
        return "ambiguous"
    if a == b:
        return "matched"
    if a <= b or b <= a:
        return "likely_match"
    overlap = len(a & b)
    if overlap >= 2:
        return "likely_match"
    if overlap == 1 and (len(a) == 1 or len(b) == 1):
        return "ambiguous"
    return "mismatch"
