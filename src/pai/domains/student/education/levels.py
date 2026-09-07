"""Evidence-backed canonical education levels.

Original qualification names remain authoritative. A canonical level is accepted
only when supplied explicitly by a verified source; free text is never mapped by
country or degree-name keyword ladders.
"""

from __future__ import annotations

from dataclasses import dataclass

CANONICAL_LEVELS = ("secondary", "higher_secondary", "bachelor", "master", "phd")
LEVEL_RANK = {level: index for index, level in enumerate(CANONICAL_LEVELS)}
LEVEL_LABEL = {
    "secondary": "secondary qualification",
    "higher_secondary": "higher secondary qualification",
    "bachelor": "bachelor-level qualification",
    "master": "master-level qualification",
    "phd": "doctoral qualification",
}
HIGHEST_LEVEL_TO_CANONICAL = {
    "high_school": "higher_secondary",
    "bachelor": "bachelor",
    "master": "master",
    "phd": "phd",
}


@dataclass(frozen=True, slots=True)
class QualificationSpec:
    canonical_level: str
    framework: str | None = None
    country: str | None = None
    label: str = ""


def classify_qualification(text: str | None) -> QualificationSpec | None:
    """Accept an explicit canonical token; never infer a level from a name."""
    token = str(text or "").strip().casefold()
    if token not in LEVEL_RANK:
        return None
    return QualificationSpec(token, label=LEVEL_LABEL[token])


def canonical_level(text: str | None) -> str | None:
    spec = classify_qualification(text)
    return spec.canonical_level if spec else None


def level_rank(level: str | None) -> int | None:
    return LEVEL_RANK.get(level or "")


def level_label(level: str | None) -> str:
    return LEVEL_LABEL.get(level or "", "this qualification")


def levels_between(lower: str, upper: str) -> list[str]:
    lo, hi = LEVEL_RANK.get(lower), LEVEL_RANK.get(upper)
    if lo is None or hi is None or hi - lo < 2:
        return []
    return list(CANONICAL_LEVELS[lo + 1:hi])
