"""Profile depth — "have-it-but-shallow" gaps the binary completion probe misses.

``vault/completion.py`` answers *whether a section has any data*. For live
counseling that is enough. For taking a student to their destination —
transcripts, CVs, statements of purpose, applications — it is not: a PhD student
with only a PhD row reads as "education complete", yet their MS / BS / FSc·
A-Levels / Matric·O-Levels are entirely unknown, and those matter downstream.

This module models that depth deterministically (no LLM, mirroring
``discovery.py`` and ``conversation_stance.py``). It produces ``DepthGap``
candidates that ``discovery.py`` folds into ranking and surfaces *only when the
turn or active goal makes them relevant* — never as an upfront questionnaire.

Additive by design: it reads typed records that are already loaded and does not
touch completion, presence, or extraction logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Education ladder, highest to lowest. FSc/A-Levels collapse to
# ``higher_secondary``; Matric/O-Levels to ``secondary``.
_LADDER: tuple[str, ...] = ("phd", "master", "bachelor", "higher_secondary", "secondary")

_RUNG_INDEX = {rung: i for i, rung in enumerate(_LADDER)}

_RUNG_LABEL: dict[str, str] = {
    "phd": "doctorate (PhD)",
    "master": "master's degree",
    "bachelor": "bachelor's degree",
    "higher_secondary": "higher secondary (FSc / A-Levels / grade 12)",
    "secondary": "secondary (Matric / O-Levels / grade 10)",
}

# Base "how much this typically matters for deliverables" weight (0..1).
_RUNG_IMPACT: dict[str, float] = {
    "phd": 0.7,
    "master": 0.6,
    "bachelor": 0.7,
    "higher_secondary": 0.5,
    "secondary": 0.4,
}

# Ordered longest/most-specific first so "higher secondary" wins before
# "secondary", and graduate rungs win before undergraduate.
_RUNG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phd", re.compile(r"\b(ph\.?d|doctor(?:al|ate)?|dphil)\b", re.I)),
    (
        "master",
        re.compile(
            r"\b(master'?s?|m\.?s\.?c?|m\.?phil|mba|m\.?a\b|m\.?tech|m\.?e\b|post[- ]?grad(?:uate)?)\b",
            re.I,
        ),
    ),
    (
        "bachelor",
        re.compile(
            r"\b(bachelor'?s?|b\.?s\.?c?s?|b\.?e\b|b\.?tech|b\.?a\b|b\.?com|b\.?b\.?a|ll\.?b|mbbs|under[- ]?grad(?:uate)?)\b",
            re.I,
        ),
    ),
    (
        "higher_secondary",
        re.compile(
            r"\b(a[- ]?levels?|f\.?sc|hssc|intermediate|higher secondary|senior secondary|"
            r"pre[- ]?(?:medical|engineering)|international baccalaureate|ib\b|diploma|"
            r"grade 12|12th|high school)\b",
            re.I,
        ),
    ),
    (
        "secondary",
        re.compile(
            r"\b(o[- ]?levels?|matric(?:ulation)?|ssc|secondary school certificate|"
            r"grade 10|10th|igcse)\b",
            re.I,
        ),
    ),
)

# education.highest_level enum -> ladder rung.
_HIGHEST_TO_RUNG: dict[str, str] = {
    "phd": "phd",
    "master": "master",
    "bachelor": "bachelor",
    "diploma": "higher_secondary",
    "high_school": "higher_secondary",
}


@dataclass(frozen=True)
class DepthGap:
    """A piece of the person that exists in outline but not in the depth needed
    to guide them all the way to their objective."""

    key: str  # synthetic, e.g. "education.level.bachelor"
    section: str  # "education" | "career"
    label: str  # short human label
    reason: str  # one-line rationale for the counselor
    impact: float  # 0..1 base weight for ranking


def classify_rung(text: str | None) -> str | None:
    """Map a degree / stream / level string to a ladder rung, or None."""
    if not text:
        return None
    for rung, pattern in _RUNG_PATTERNS:
        if pattern.search(str(text)):
            return rung
    return None


def _row_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in ("degree", "major", "level", "stream", "institution")
    )


def _present_rungs(educations: list[dict[str, Any]]) -> set[str]:
    present: set[str] = set()
    for row in educations:
        rung = classify_rung(_row_text(row))
        if rung:
            present.add(rung)
    return present


def _ladder_gaps(highest_level: str | None, educations: list[dict[str, Any]]) -> list[DepthGap]:
    present = _present_rungs(educations)
    # Anchor: the highest rung we know they reached (from the enum, or from the
    # highest classified row).
    anchor = _HIGHEST_TO_RUNG.get((highest_level or "").strip().lower())
    if anchor is None and present:
        anchor = min(present, key=lambda r: _RUNG_INDEX[r])
    if anchor is None:
        return []  # nothing to anchor on; normal catalog discovery handles first-contact
    anchor_idx = _RUNG_INDEX[anchor]
    gaps: list[DepthGap] = []
    for rung in _LADDER[anchor_idx:]:
        if rung in present:
            continue
        gaps.append(
            DepthGap(
                key=f"education.level.{rung}",
                section="education",
                label=_RUNG_LABEL[rung],
                reason=(
                    f"their {_RUNG_LABEL[rung]} isn't on file yet — earlier education "
                    "matters for transcripts, eligibility, and statements of purpose"
                ),
                impact=_RUNG_IMPACT[rung],
            )
        )
    return gaps


def _has(value: Any) -> bool:
    return value not in (None, "", [], {})


def _education_detail_gaps(educations: list[dict[str, Any]]) -> list[DepthGap]:
    gaps: list[DepthGap] = []
    for row in educations:
        rung = classify_rung(_row_text(row))
        label = _RUNG_LABEL.get(rung or "", "one of their degrees")
        # Only the single most useful missing attribute per row, to avoid noise.
        if not _has(row.get("institution")):
            missing, attr, impact = "the institution", "institution", 0.5
        elif not (_has(row.get("graduationYear")) or _has(row.get("endDate"))):
            missing, attr, impact = "the dates/graduation year", "dates", 0.4
        elif not (_has(row.get("gpa")) or _has(row.get("percentage"))):
            missing, attr, impact = "the GPA/marks", "grades", 0.5
        elif not _has(row.get("major")):
            missing, attr, impact = "the major/field", "major", 0.35
        else:
            continue
        anchor = rung or f"row{len(gaps)}"
        gaps.append(
            DepthGap(
                key=f"education.detail.{anchor}.{attr}",
                section="education",
                label=f"{label} details",
                reason=(
                    f"you have their {label} but not {missing} — needed to write it up "
                    "accurately for applications"
                ),
                impact=impact,
            )
        )
    return gaps


def _career_detail_gaps(work_experiences: list[dict[str, Any]]) -> list[DepthGap]:
    gaps: list[DepthGap] = []
    for row in work_experiences:
        org = row.get("organization") or "one of their roles"
        if not _has(row.get("description")):
            gaps.append(
                DepthGap(
                    key="career.detail.description",
                    section="career",
                    label=f"role at {org}",
                    reason=(
                        f"you know they worked at {org} but not what they actually did — "
                        "the specifics matter for CVs and interviews"
                    ),
                    impact=0.45,
                )
            )
    return gaps


def compute_depth_gaps(
    *,
    highest_level: str | None = None,
    educations: list[dict[str, Any]] | None = None,
    work_experiences: list[dict[str, Any]] | None = None,
    max_gaps: int = 8,
) -> list[DepthGap]:
    """All depth gaps for a profile, ordered education-ladder → education-detail
    → career-detail. Deterministic and side-effect free.

    This does not decide *whether* to surface anything — that is discovery's job,
    based on the current turn's relevance. It only enumerates what depth is
    missing given data already on record.
    """
    educations = educations or []
    work_experiences = work_experiences or []
    gaps: list[DepthGap] = []
    gaps.extend(_ladder_gaps(highest_level, educations))
    gaps.extend(_education_detail_gaps(educations))
    gaps.extend(_career_detail_gaps(work_experiences))

    # Dedupe by key, preserve order.
    seen: set[str] = set()
    out: list[DepthGap] = []
    for gap in gaps:
        if gap.key in seen:
            continue
        seen.add(gap.key)
        out.append(gap)
    return out[:max_gaps]
