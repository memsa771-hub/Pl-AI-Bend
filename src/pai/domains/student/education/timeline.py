"""Education timeline ordering and validation.

Education is an ordered academic history, not a bag of rows (doc §6). This
module puts records in sequence and reports what looks wrong with the sequence.

It detects suspicious patterns; it does not impose a worldview (doc §11). Gaps,
overlaps and skipped stages are all legitimate in some systems, so findings are
reported with a severity and a natural clarifying question, and the counselor
decides — via discovery ranking — whether any of it is worth asking about.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pai.domains.student.education.levels import (
    LEVEL_RANK,
    canonical_level,
    level_label,
    levels_between,
)
from pai.domains.student.education.resolver import institution_similarity
from pai.domains.student.issues.service import DetectedIssue

DOMAIN = "education"

# Issue types this validator is authoritative for; re-scanning may auto-resolve
# these and nothing else.
TIMELINE_ISSUE_TYPES = frozenset(
    {
        "missing_stage",
        "missing_dates",
        "date_gap",
        "date_overlap",
        "duplicate_entity",
        "impossible_transition",
    }
)

# A gap shorter than this is ordinary (results, admissions cycles, a short break).
_GAP_MONTHS = 18
_DUPLICATE_INSTITUTION_OVERLAP = 0.6


def row_level(row: Any) -> str | None:
    """Canonical level of an education row, falling back to its wording."""
    stored = getattr(row, "canonical_level", None)
    if stored:
        return str(stored)
    return canonical_level(
        " ".join(
            str(getattr(row, key, "") or "") for key in ("degree", "original_name", "major")
        )
    )


def _start_year(row: Any) -> int | None:
    start = getattr(row, "start_date", None)
    return start.year if isinstance(start, date) else None


def _end_year(row: Any) -> int | None:
    end = getattr(row, "end_date", None)
    if isinstance(end, date):
        return end.year
    year = getattr(row, "graduation_year", None)
    return int(year) if year else None


def _sort_key(row: Any) -> tuple[int, int]:
    """Order by time where known, else by canonical level."""
    level = row_level(row)
    rank = LEVEL_RANK.get(level or "", len(LEVEL_RANK))
    return (_start_year(row) or _end_year(row) or 9999, rank)


def order_timeline(rows: list[Any]) -> list[Any]:
    """Education records in academic order, earliest first.

    Sequence is derived, never stored: dates and canonical level already
    determine it, and a stored order would just be a second source of truth to
    keep in sync (doc §8).
    """
    return sorted(rows, key=_sort_key)


def _months_between(earlier_year: int, later_year: int) -> int:
    return (later_year - earlier_year) * 12


def _missing_stage_issues(ordered: list[Any]) -> list[DetectedIssue]:
    """Stages absent between two known stages — never assumed, only flagged (doc §12)."""
    known = [(row, row_level(row)) for row in ordered]
    leveled = [(row, level) for row, level in known if level]
    issues: list[DetectedIssue] = []
    for (lower_row, lower), (upper_row, upper) in zip(leveled, leveled[1:]):
        for missing in levels_between(lower, upper):
            period_from = _end_year(lower_row)
            period_to = _start_year(upper_row) or _end_year(upper_row)
            issues.append(
                DetectedIssue(
                    domain=DOMAIN,
                    issue_type="missing_stage",
                    fingerprint=f"{DOMAIN}:missing_stage:{missing}",
                    severity="medium",
                    confidence=0.7,
                    related_entity_type="education",
                    related_entity_ids=[str(lower_row.id), str(upper_row.id)],
                    detail={
                        "missingLevel": missing,
                        "between": [lower, upper],
                        "periodFrom": period_from,
                        "periodTo": period_to,
                    },
                    clarification_needed=True,
                    clarification_prompt=(
                        f"I have their {level_label(lower)} and {level_label(upper)}, "
                        f"but not what they studied in between"
                        + (f" ({period_from}–{period_to})" if period_from and period_to else "")
                        + " — ask what qualification they took at that stage."
                    ),
                )
            )
    return issues


def _missing_date_issues(rows: list[Any]) -> list[DetectedIssue]:
    issues: list[DetectedIssue] = []
    for row in rows:
        if _start_year(row) or _end_year(row):
            continue
        label = level_label(row_level(row))
        issues.append(
            DetectedIssue(
                domain=DOMAIN,
                issue_type="missing_dates",
                fingerprint=f"{DOMAIN}:missing_dates:{row.id}",
                severity="low",
                confidence=0.9,
                related_entity_type="education",
                related_entity_ids=[str(row.id)],
                detail={"institution": row.institution, "level": row_level(row)},
                clarification_needed=True,
                clarification_prompt=(
                    f"their {label} at {row.institution} has no dates on file — "
                    "ask when they studied there"
                ),
            )
        )
    return issues


def _gap_and_overlap_issues(ordered: list[Any]) -> list[DetectedIssue]:
    issues: list[DetectedIssue] = []
    for earlier, later in zip(ordered, ordered[1:]):
        earlier_end, later_start = _end_year(earlier), _start_year(later)
        if earlier_end is None or later_start is None:
            continue
        span = _months_between(earlier_end, later_start)
        if span >= _GAP_MONTHS:
            issues.append(
                DetectedIssue(
                    domain=DOMAIN,
                    issue_type="date_gap",
                    fingerprint=f"{DOMAIN}:date_gap:{earlier.id}:{later.id}",
                    severity="low",
                    confidence=0.6,
                    related_entity_type="education",
                    related_entity_ids=[str(earlier.id), str(later.id)],
                    detail={"from": earlier_end, "to": later_start, "months": span},
                    clarification_needed=False,
                    clarification_prompt=(
                        f"there's a {earlier_end}–{later_start} gap in their education — "
                        "worth understanding only if it affects an application"
                    ),
                )
            )
        elif span < 0:
            issues.append(
                DetectedIssue(
                    domain=DOMAIN,
                    issue_type="date_overlap",
                    fingerprint=f"{DOMAIN}:date_overlap:{earlier.id}:{later.id}",
                    severity="low",
                    confidence=0.5,
                    related_entity_type="education",
                    related_entity_ids=[str(earlier.id), str(later.id)],
                    detail={"earlierEnd": earlier_end, "laterStart": later_start},
                    clarification_needed=False,
                    clarification_prompt=(
                        "two of their qualifications overlap in time — often fine, "
                        "but the dates may be wrong"
                    ),
                )
            )
    return issues


def _duplicate_issues(rows: list[Any]) -> list[DetectedIssue]:
    issues: list[DetectedIssue] = []
    for i, first in enumerate(rows):
        for second in rows[i + 1 :]:
            level_a, level_b = row_level(first), row_level(second)
            if not level_a or level_a != level_b:
                continue
            similarity = institution_similarity(first.institution, second.institution)
            if similarity < _DUPLICATE_INSTITUTION_OVERLAP:
                continue
            pair = sorted((str(first.id), str(second.id)))
            issues.append(
                DetectedIssue(
                    domain=DOMAIN,
                    issue_type="duplicate_entity",
                    fingerprint=f"{DOMAIN}:duplicate_entity:{pair[0]}:{pair[1]}",
                    severity="high",
                    confidence=0.7,
                    related_entity_type="education",
                    related_entity_ids=pair,
                    detail={
                        "level": level_a,
                        "institutions": [first.institution, second.institution],
                    },
                    clarification_needed=False,
                    clarification_prompt=(
                        f"two {level_label(level_a)} records look like the same "
                        "qualification recorded twice"
                    ),
                )
            )
    return issues


def _impossible_transition_issues(ordered: list[Any]) -> list[DetectedIssue]:
    issues: list[DetectedIssue] = []
    for earlier, later in zip(ordered, ordered[1:]):
        level_a, level_b = row_level(earlier), row_level(later)
        if not level_a or not level_b:
            continue
        rank_a, rank_b = LEVEL_RANK.get(level_a), LEVEL_RANK.get(level_b)
        if rank_a is None or rank_b is None or rank_b >= rank_a:
            continue
        # A lower stage starting after a higher one finished is unusual, though
        # a genuine second qualification later in life would look identical.
        issues.append(
            DetectedIssue(
                domain=DOMAIN,
                issue_type="impossible_transition",
                fingerprint=f"{DOMAIN}:impossible_transition:{earlier.id}:{later.id}",
                severity="medium",
                confidence=0.5,
                related_entity_type="education",
                related_entity_ids=[str(earlier.id), str(later.id)],
                detail={"after": level_a, "then": level_b},
                clarification_needed=False,
                clarification_prompt=(
                    f"their {level_label(level_b)} appears to come after their "
                    f"{level_label(level_a)} — the dates may need checking"
                ),
            )
        )
    return issues


def validate_education_timeline(rows: list[Any]) -> list[DetectedIssue]:
    """All timeline findings for a student's education records."""
    if not rows:
        return []
    ordered = order_timeline(rows)
    issues: list[DetectedIssue] = []
    issues.extend(_missing_stage_issues(ordered))
    issues.extend(_missing_date_issues(ordered))
    issues.extend(_gap_and_overlap_issues(ordered))
    issues.extend(_duplicate_issues(ordered))
    issues.extend(_impossible_transition_issues(ordered))

    deduped: dict[str, DetectedIssue] = {}
    for issue in issues:
        deduped.setdefault(issue.fingerprint, issue)
    return list(deduped.values())
