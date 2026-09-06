"""Profile Discovery / Gap Selection — deterministic ranking of missing Vault
fields by relevance to the current turn (PAI_Intelligent_Counselor_Architecture.md §7/§20).

Vault Completion (`vault/completion.py`) is the source of truth on *what* is
missing and its C/I/E priority. This layer decides *which one*, if any, is
worth surfacing to the counselor this turn — relevance to the current message
and active goal beats raw priority order, per the architecture doc's Rule 8.

No LLM call. Pure, deterministic scoring so results are stable and testable,
mirroring the style of `rank_score()` in `domains/memory/formation.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pai.domains.student.vault.catalog import VAULT_CATALOG, CatalogField, Priority
from pai.intelligences.counselor.profile_depth import DepthGap

_PRIORITY_WEIGHT: dict[Priority, float] = {"C": 1.0, "I": 0.6, "E": 0.3}

# Static "expected decision impact" weights — how much this fact typically
# changes concrete counseling advice (fees comparisons, eligibility, fit).
# Fields absent here fall back to _DEFAULT_IMPACT.
_IMPACT_WEIGHT: dict[str, float] = {
    "location.current_country": 0.9,
    "location.current_city": 0.5,
    "application.study_country": 0.9,
    "education.highest_level": 0.85,
    "education.records": 0.8,
    "education.program": 0.75,
    "education.gpa": 0.7,
    "finance.funding_status": 0.85,
    "application.test_scores": 0.7,
    "application.target_universities": 0.55,
    "application.admission_cycle": 0.4,
    "career.work_history": 0.5,
    "career.projects": 0.5,
    "career.skills": 0.45,
    "career.certifications": 0.35,
    "identity.current_status": 0.6,
    "mobility.relocation_willingness": 0.4,
    "mobility.preferred_regions": 0.3,
    "finance.scholarship_interest": 0.4,
    "preferences.learning_style": 0.25,
    "preferences.communication_style": 0.2,
    "preferences.preferred_language": 0.2,
    "social.linkedin_url": 0.15,
    "lifestyle.work_life_balance": 0.2,
    "demographics.nationality": 0.35,
    "demographics.gender": 0.15,
}
_DEFAULT_IMPACT = 0.35

# Deliberately small, deterministic keyword sets — mirrors the lightweight
# regex approach already used in counselor/routing.py. Not exhaustive by
# design: a false negative just means no discovery candidate fires, which is
# the safe failure mode (Rule 5 — never turn counseling into a questionnaire).
_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "location": ("live", "based in", "city", "country", "located", "move to", "relocate"),
    "education": (
        "gpa", "cgpa", "degree", "study", "school", "college", "university",
        "grade", "marks", "transcript", "bachelor", "master", "phd",
    ),
    "career": ("job", "work", "internship", "project", "skill", "experience", "resume", "cv"),
    "application": (
        "apply", "admission", "university", "program", "test", "ielts",
        "toefl", "gre", "gmat", "deadline", "intake", "scholarship",
    ),
    "finance": (
        "budget", "afford", "cost", "tuition", "scholarship", "fund",
        "loan", "money", "expensive", "cheap", "financ",
    ),
    "mobility": ("visa", "relocate", "move", "passport", "region"),
    "identity": ("student", "graduate", "working", "employed", "status"),
    "demographics": ("gender", "nationality", "age", "born"),
    "preferences": ("prefer", "like", "enjoy", "style"),
    "lifestyle": ("balance", "lifestyle"),
    "social": ("linkedin",),
    "family": ("family", "parents", "sponsor"),
    "accessibility": ("disability", "accommodation"),
}

# Which Vault sections matter most for each goal type — doc §6 example
# (comparing AI vs cybersecurity: education/career gaps outrank an unrelated
# Critical field).
_GOAL_TYPE_SECTIONS: dict[str, tuple[str, ...]] = {
    "admission": ("application", "education", "finance", "mobility", "location"),
    "job": ("career", "identity", "location", "mobility"),
    "internship": ("career", "identity", "location"),
    "general": (),
}

# Recently-asked candidates are suppressed for this long even if still
# unanswered, so the counselor cycles through different gaps instead of
# hammering the same one turn after turn (Rule 6/Rule 7).
_RECENTLY_ASKED_WINDOW_SECONDS = 3 * 24 * 3600.0


@dataclass(frozen=True)
class DiscoveryCandidate:
    field_key: str
    priority: Priority
    section: str
    score: float
    reasons: dict[str, float] = field(default_factory=dict)
    # "field" = a missing catalog field; "depth" = a have-it-but-shallow gap
    # (e.g. an earlier degree) surfaced by profile_depth.py.
    kind: str = "field"
    label: str | None = None
    reason_text: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    top: DiscoveryCandidate | None
    runners_up: list[DiscoveryCandidate]
    missing_important: list[str]
    enrichment_opportunities: list[str]


def _message_relevance(message: str, section: str) -> float:
    text = (message or "").casefold()
    if not text:
        return 0.0
    keywords = _SECTION_KEYWORDS.get(section, ())
    return 1.0 if any(kw in text for kw in keywords) else 0.0


def _goal_relevance(goal_type: str | None, section: str) -> float:
    if not goal_type:
        return 0.0
    return 1.0 if section in _GOAL_TYPE_SECTIONS.get(goal_type, ()) else 0.0


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def score_field(
    field_obj: CatalogField,
    *,
    message: str,
    goal_type: str | None,
    is_recently_asked: bool,
) -> DiscoveryCandidate:
    """Doc §20 formula, made concrete with fixed weights.

    score = 0.35*message_relevance + 0.20*goal_relevance + 0.20*priority
          + 0.25*expected_impact - 0.90*recently_asked_penalty
    """
    msg_rel = _message_relevance(message, field_obj.section)
    goal_rel = _goal_relevance(goal_type, field_obj.section)
    priority_w = _PRIORITY_WEIGHT[field_obj.priority]
    impact_w = _IMPACT_WEIGHT.get(field_obj.key, _DEFAULT_IMPACT)
    recently_asked_penalty = 1.0 if is_recently_asked else 0.0

    score = (
        0.35 * msg_rel
        + 0.20 * goal_rel
        + 0.20 * priority_w
        + 0.25 * impact_w
        - 0.90 * recently_asked_penalty
    )
    return DiscoveryCandidate(
        field_key=field_obj.key,
        priority=field_obj.priority,
        section=field_obj.section,
        score=round(score, 4),
        reasons={
            "message_relevance": msg_rel,
            "goal_relevance": goal_rel,
            "priority_weight": priority_w,
            "impact_weight": impact_w,
            "recently_asked_penalty": recently_asked_penalty,
        },
    )


def score_depth_gap(
    gap: DepthGap,
    *,
    message: str,
    goal_type: str | None,
) -> DiscoveryCandidate | None:
    """Score a profile-depth gap. Returns None unless the turn or active goal
    makes it relevant — depth is never surfaced upfront, only when needed.

    No priority term (depth gaps are not catalog fields) and no upfront base, so
    a depth gap can only win when message/goal relevance fires — then relevance
    lets it outrank an unrelated catalog field, per Rule 8.
    """
    msg_rel = _message_relevance(message, gap.section)
    goal_rel = _goal_relevance(goal_type, gap.section)
    if msg_rel == 0.0 and goal_rel == 0.0:
        return None
    score = 0.35 * msg_rel + 0.20 * goal_rel + 0.22 * gap.impact
    return DiscoveryCandidate(
        field_key=gap.key,
        priority="I",
        section=gap.section,
        score=round(score, 4),
        reasons={
            "message_relevance": msg_rel,
            "goal_relevance": goal_rel,
            "impact_weight": gap.impact,
            "depth": 1.0,
        },
        kind="depth",
        label=gap.label,
        reason_text=gap.reason,
    )


def select_discovery_candidates(
    *,
    missing_critical: list[str] | None = None,
    missing_important: list[str] | None = None,
    missing_enrichment: list[str] | None = None,
    depth_gaps: Sequence[DepthGap] | None = None,
    message: str = "",
    goal_type: str | None = None,
    known_facts: list[str] | None = None,
    recently_asked_field_key: str | None = None,
    recently_asked_at: datetime | None = None,
    now: datetime | None = None,
    max_runners_up: int = 2,
) -> DiscoveryResult:
    """Rank missing Vault fields by relevance to *this* turn.

    Priority alone no longer decides ranking — relevance does (Rule 8). The
    caller passes the raw missing-field lists per priority (already computed
    by `vault/completion.py`); this function decides which single field, if
    any, is worth surfacing.
    """
    known_blob = " | ".join(str(item).casefold() for item in (known_facts or []))
    stamp = now or datetime.now(UTC)
    is_recent = (
        recently_asked_field_key is not None
        and recently_asked_at is not None
        and (stamp - _aware(recently_asked_at)).total_seconds() < _RECENTLY_ASKED_WINDOW_SECONDS
    )

    important_out: list[str] = []
    enrichment_out: list[str] = []
    candidates: list[DiscoveryCandidate] = []
    grouped = (
        (missing_critical or [], None),
        (missing_important or [], important_out),
        (missing_enrichment or [], enrichment_out),
    )
    for keys, bucket in grouped:
        for key in keys:
            field_obj = VAULT_CATALOG.get(key)
            if field_obj is None or field_obj.derived or not field_obj.editable:
                continue
            suffix_label = field_obj.key.split(".", 1)[-1].replace("_", " ").casefold()
            if suffix_label and known_blob and suffix_label in known_blob:
                continue
            if bucket is not None:
                bucket.append(key)
            candidates.append(
                score_field(
                    field_obj,
                    message=message,
                    goal_type=goal_type,
                    is_recently_asked=is_recent and key == recently_asked_field_key,
                )
            )

    for gap in depth_gaps or ():
        depth_candidate = score_depth_gap(gap, message=message, goal_type=goal_type)
        if depth_candidate is not None:
            candidates.append(depth_candidate)

    candidates.sort(key=lambda c: c.score, reverse=True)
    top = candidates[0] if candidates and candidates[0].score > 0 else None
    runners_up = list(candidates[1 : 1 + max_runners_up]) if candidates else []
    return DiscoveryResult(
        top=top,
        runners_up=runners_up,
        missing_important=important_out[:4],
        enrichment_opportunities=enrichment_out[:2],
    )


def explain(candidate: DiscoveryCandidate) -> str:
    """One-line, human-readable rationale for the top candidate (doc §12)."""
    if candidate.kind == "depth" and candidate.reason_text:
        return candidate.reason_text
    label = candidate.field_key.split(".")[-1].replace("_", " ")
    reasons = candidate.reasons
    why: list[str] = []
    if reasons.get("message_relevance"):
        why.append("relevant to what you just asked")
    if reasons.get("goal_relevance"):
        why.append("relevant to your active goal")
    if not why:
        why.append("would sharpen overall guidance")
    return f"{label} — {', '.join(why)}"
