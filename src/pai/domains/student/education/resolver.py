"""Education entity resolution.

Every academic observation must be resolved to the education record it actually
describes before it is persisted (doc §6, §10). Without this, "my FSc was 82%"
either lands on the wrong row or spawns a duplicate record when the transcript
arrives later (doc §29).

The resolver is deterministic and scores each existing record; when two records
are close enough that picking one would be a guess, it reports ambiguity rather
than choosing (doc §10: "PAI should not guess").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pai.domains.student.education.levels import canonical_level

# A record must clear this to be considered the same entity at all.
MATCH_THRESHOLD = 0.45
# ...and must beat the runner-up by this, otherwise the observation is ambiguous.
AMBIGUITY_MARGIN = 0.15

# Dropped before comparing institution names: they carry no distinguishing
# signal and would otherwise make every university look alike.
_INSTITUTION_STOPWORDS = frozenset(
    {
        "university", "univ", "college", "school", "institute", "institution",
        "academy", "campus", "of", "the", "and", "for", "group", "national",
        "govt", "government", "public", "private", "higher", "secondary",
    }
)

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _tokens(text: Any) -> frozenset[str]:
    if not text:
        return frozenset()
    words = _NON_WORD.sub(" ", str(text).casefold()).split()
    return frozenset(w for w in words if w and w not in _INSTITUTION_STOPWORDS)


def _norm(text: Any) -> str:
    if not text:
        return ""
    return _NON_WORD.sub(" ", str(text).casefold()).strip()


def _overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Containment, not Jaccard.

    Institutions are routinely written at different lengths ("Punjab College"
    vs "Punjab College Lahore"), and Jaccard penalizes the longer form for
    carrying more words. Containment asks the question that actually matters:
    is the shorter name entirely present in the longer one?
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def institution_similarity(a: Any, b: Any) -> float:
    """0..1 name similarity between two institutions, ignoring generic words."""
    return _overlap(_tokens(a), _tokens(b))


def _year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 < year < 2100 else None


@dataclass(frozen=True, slots=True)
class ResolutionOutcome:
    """Which existing record an observation describes, and how sure we are."""

    match: Any | None
    confidence: float
    ambiguous: bool
    rivals: list[Any] = field(default_factory=list)
    reason: str = ""


def observation_level(payload: dict[str, Any]) -> str | None:
    """Canonical level implied by an education observation, if any."""
    for key in ("canonical_level", "degree", "original_name", "major", "qualification"):
        level = (
            payload.get(key)
            if key == "canonical_level"
            else canonical_level(payload.get(key))
        )
        if level:
            return str(level)
    return None


def _row_level(row: Any) -> str | None:
    return getattr(row, "canonical_level", None) or canonical_level(
        " ".join(
            str(getattr(row, k, "") or "")
            for k in ("degree", "original_name", "major")
        )
    )


def _row_year(row: Any) -> int | None:
    year = _year(getattr(row, "graduation_year", None))
    if year is not None:
        return year
    end = getattr(row, "end_date", None)
    return end.year if end is not None else None


def score_record(payload: dict[str, Any], row: Any) -> float | None:
    """Similarity of an observation to one existing education record.

    Returns None when the record is disqualified — a different canonical level
    or an incompatible graduation year means this cannot be the same entity, no
    matter how well the other attributes line up.
    """
    obs_level = observation_level(payload)
    row_level = _row_level(row)
    if obs_level and row_level and obs_level != row_level:
        return None

    obs_year = _year(payload.get("graduation_year"))
    row_year = _row_year(row)
    if obs_year and row_year and abs(obs_year - row_year) > 1:
        return None

    score = 0.0
    if obs_level and row_level:
        score += 0.45

    obs_inst, row_inst = _norm(payload.get("institution")), _norm(row.institution)
    if obs_inst and row_inst:
        if obs_inst == row_inst:
            # Enough on its own to resolve. If the student attended the same
            # institution twice, both rows score equally and the tie is
            # reported as ambiguous rather than resolved to the wrong one.
            score += 0.50
        else:
            overlap = _overlap(_tokens(obs_inst), _tokens(row_inst))
            if overlap >= 0.5:
                score += 0.22
            elif overlap > 0:
                score += 0.08

    obs_degree, row_degree = _norm(payload.get("degree")), _norm(row.degree)
    if obs_degree and row_degree:
        if obs_degree == row_degree:
            score += 0.25
        elif _overlap(_tokens(obs_degree), _tokens(row_degree)) >= 0.5:
            score += 0.12

    obs_major, row_major = _norm(payload.get("major")), _norm(row.major)
    if obs_major and row_major and obs_major == row_major:
        score += 0.15

    if obs_year and row_year:
        score += 0.20 if obs_year == row_year else 0.08

    return round(score, 4)


def _is_bare_measurement(payload: dict[str, Any]) -> bool:
    """True when the observation carries a grade but nothing that identifies a record."""
    identifying = ("institution", "degree", "major", "canonical_level", "graduation_year")
    measurements = ("gpa", "percentage", "gpa_scale")
    return not any(payload.get(k) for k in identifying) and any(
        payload.get(k) is not None for k in measurements
    )


def resolve_education(payload: dict[str, Any], rows: list[Any]) -> ResolutionOutcome:
    """Resolve an education observation against the student's existing records."""
    if not rows:
        return ResolutionOutcome(None, 0.0, False, reason="no_existing_records")

    if _is_bare_measurement(payload):
        # "I got 82%" with no other signal. One record means it is unambiguous;
        # several means guessing, which is exactly what doc §10 forbids.
        if len(rows) == 1:
            return ResolutionOutcome(rows[0], 0.6, False, reason="only_record")
        return ResolutionOutcome(
            None, 0.0, True, rivals=list(rows), reason="bare_measurement_many_records"
        )

    scored = [(score, row) for row in rows if (score := score_record(payload, row)) is not None]
    if not scored:
        return ResolutionOutcome(None, 0.0, False, reason="no_compatible_record")

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_row = scored[0]
    if best_score < MATCH_THRESHOLD:
        return ResolutionOutcome(None, best_score, False, reason="below_threshold")

    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - runner_up < AMBIGUITY_MARGIN:
        return ResolutionOutcome(
            None,
            best_score,
            True,
            rivals=[row for _score, row in scored[:2]],
            reason="tied_candidates",
        )
    return ResolutionOutcome(best_row, best_score, False, reason="resolved")
