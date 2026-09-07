"""Canonical education levels — country-agnostic qualification normalization.

No single country's education system is the universal one (architecture doc §7,
§39). A qualification therefore keeps the name the student actually used and is
*additionally* mapped onto a canonical level shared across frameworks, so
"FSc Pre-Engineering" (HSSC, Pakistan), "A-Levels" (GCE), "Grade 12" (US) and
"CBSE 12th" (India) all resolve to ``higher_secondary`` without any one of them
becoming the definition of that stage.

Adding a country or board is a data edit to ``QUALIFICATIONS`` — no logic
changes. Deterministic and side-effect free, mirroring ``profile_depth.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Ascending order. These are the stages PAI reasons about; they are deliberately
# coarse so that unfamiliar national systems can still land somewhere sensible.
CANONICAL_LEVELS: tuple[str, ...] = (
    "secondary",
    "higher_secondary",
    "bachelor",
    "master",
    "phd",
)

LEVEL_RANK: dict[str, int] = {level: i for i, level in enumerate(CANONICAL_LEVELS)}

LEVEL_LABEL: dict[str, str] = {
    "phd": "doctorate (PhD)",
    "master": "master's degree",
    "bachelor": "bachelor's degree",
    "higher_secondary": "higher secondary (FSc / A-Levels / grade 12)",
    "secondary": "secondary (Matric / O-Levels / grade 10)",
}

# `education.highest_level` catalog enum -> canonical level.
HIGHEST_LEVEL_TO_CANONICAL: dict[str, str] = {
    "phd": "phd",
    "master": "master",
    "bachelor": "bachelor",
    "diploma": "higher_secondary",
    "high_school": "higher_secondary",
}


@dataclass(frozen=True, slots=True)
class QualificationSpec:
    """One recognizable qualification family."""

    canonical_level: str
    framework: str | None
    country: str | None
    label: str
    pattern: re.Pattern[str]


def _rx(source: str) -> re.Pattern[str]:
    return re.compile(source, re.I)


# Ordered most-specific first: the first match wins, so graduate degrees are
# tested before undergraduate ones and named boards before generic wording.
# `country` is ISO-3166 alpha-2 where a qualification belongs to one national
# system, and None where it is used internationally.
QUALIFICATIONS: tuple[QualificationSpec, ...] = (
    QualificationSpec(
        "phd", None, None, "Doctorate",
        _rx(r"\b(ph\.?d|doctor(?:al|ate)?|dphil)\b"),
    ),
    QualificationSpec(
        "master", None, None, "Master's degree",
        _rx(
            r"\b(master'?s?|m\.?s\.?c?|m\.?phil|mba|m\.?a\b|m\.?tech|m\.?e\b|"
            r"post[- ]?grad(?:uate)?)\b"
        ),
    ),
    QualificationSpec(
        "bachelor", None, None, "Bachelor's degree",
        _rx(
            r"\b(bachelor'?s?|b\.?s\.?c?s?|b\.?e\b|b\.?tech|b\.?a\b|b\.?com|"
            r"b\.?b\.?a|ll\.?b|mbbs|under[- ]?grad(?:uate)?)\b"
        ),
    ),
    QualificationSpec(
        "higher_secondary", "HSSC", "PK", "Higher Secondary School Certificate",
        _rx(
            r"\b(f\.?sc|f\.?a\b|i\.?c\.?s\b|i\.?com\b|hssc|intermediate|"
            r"pre[- ]?(?:medical|engineering))\b"
        ),
    ),
    QualificationSpec(
        "higher_secondary", "GCE A-Level", None, "A-Levels",
        _rx(r"\b(a[- ]?levels?|as[- ]?levels?|gce advanced)\b"),
    ),
    QualificationSpec(
        "higher_secondary", "IB Diploma", None, "IB Diploma",
        _rx(r"\b(international baccalaureate|ib diploma|ibdp|\bib\b)"),
    ),
    QualificationSpec(
        "higher_secondary", "CBSE/ISC", "IN", "Class 12 (India)",
        _rx(r"\b(class 12|12th standard|hsc|isc|cbse 12)\b"),
    ),
    QualificationSpec(
        "higher_secondary", "US High School", "US", "High School Diploma",
        _rx(r"\b(high school diploma|advanced placement|\bap exams?\b)\b"),
    ),
    QualificationSpec(
        "higher_secondary", None, None, "Higher secondary",
        _rx(r"\b(higher secondary|senior secondary|diploma|grade 12|12th|high school)\b"),
    ),
    QualificationSpec(
        "secondary", "SSC", "PK", "Secondary School Certificate",
        _rx(r"\b(matric(?:ulation)?|ssc)\b"),
    ),
    QualificationSpec(
        "secondary", "GCSE/O-Level", None, "O-Levels",
        _rx(r"\b(o[- ]?levels?|igcse|gcse)\b"),
    ),
    QualificationSpec(
        "secondary", "CBSE/ICSE", "IN", "Class 10 (India)",
        _rx(r"\b(class 10|10th standard|icse|cbse 10)\b"),
    ),
    QualificationSpec(
        "secondary", None, None, "Secondary",
        _rx(r"\b(secondary school certificate|grade 10|10th)\b"),
    ),
)


def classify_qualification(text: str | None) -> QualificationSpec | None:
    """Map free-text qualification wording to a known qualification family."""
    if not text:
        return None
    haystack = str(text)
    for spec in QUALIFICATIONS:
        if spec.pattern.search(haystack):
            return spec
    return None


def canonical_level(text: str | None) -> str | None:
    """Canonical level for free-text qualification wording, or None."""
    spec = classify_qualification(text)
    return spec.canonical_level if spec else None


def level_rank(level: str | None) -> int | None:
    return LEVEL_RANK.get(level) if level else None


def level_label(level: str | None) -> str:
    return LEVEL_LABEL.get(level or "", "this qualification")


def levels_between(lower: str, upper: str) -> list[str]:
    """Canonical levels strictly between two levels, ascending."""
    lo, hi = LEVEL_RANK.get(lower), LEVEL_RANK.get(upper)
    if lo is None or hi is None or hi - lo < 2:
        return []
    return list(CANONICAL_LEVELS[lo + 1 : hi])
