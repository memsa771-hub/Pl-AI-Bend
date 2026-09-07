"""Candidate enumeration; a semantic intervention chooses whether to ask."""
from dataclasses import dataclass, field
from pai.domains.student.vault.catalog import VAULT_CATALOG

@dataclass(frozen=True)
class DiscoveryCandidate:
    field_key: str
    priority: str
    section: str
    score: float = 0.0
    reasons: dict = field(default_factory=dict)
    kind: str = "field"
    label: str | None = None
    reason_text: str | None = None
    issue_id: str | None = None

@dataclass(frozen=True)
class DiscoveryResult:
    top: DiscoveryCandidate | None
    runners_up: list[DiscoveryCandidate]
    missing_important: list[str]
    enrichment_opportunities: list[str]

def score_field(field_obj, **kwargs):
    return DiscoveryCandidate(field_obj.key, field_obj.priority, field_obj.section)

def score_depth_gap(gap, **kwargs):
    return DiscoveryCandidate(gap.key, "I", gap.section, kind="depth",
                              label=gap.label, reason_text=gap.reason)

def score_issue(issue, **kwargs):
    """Surface unresolved contradictions by severity, without field-name weights."""
    severity = {"high": 1.0, "medium": 0.6, "low": 0.3}.get(issue.severity, 0.3)
    actionable = 1.0 if issue.clarification_needed else 0.25
    return DiscoveryCandidate(
        f"issue.{issue.domain}.{issue.issue_type}", "I", issue.domain,
        score=severity * actionable, reasons={"issue": 1.0, "severity": severity},
        kind="issue", label=issue.issue_type.replace("_", " "),
        reason_text=issue.clarification_prompt, issue_id=str(issue.id),
    )

def select_discovery_candidates(*, missing_critical=None, missing_important=None,
        missing_enrichment=None, depth_gaps=None, issues=None, recently_asked_field_key=None,
        recently_asked_at=None, now=None, **kwargs):
    from datetime import UTC, datetime, timedelta
    stamp = now or datetime.now(UTC)
    recent = recently_asked_at
    if recent and recent.tzinfo is None:
        recent = recent.replace(tzinfo=UTC)
    suppressed = recently_asked_field_key if recent and stamp-recent < timedelta(days=3) else None
    candidates = []
    for key in dict.fromkeys([*(missing_critical or []), *(missing_important or []), *(missing_enrichment or [])]):
        item = VAULT_CATALOG.get(key)
        if item and item.editable and not item.derived and key != suppressed:
            candidates.append(score_field(item))
    candidates.extend(score_depth_gap(gap) for gap in depth_gaps or [] if gap.key != suppressed)
    candidates.extend(score_issue(issue) for issue in issues or [])
    candidates.sort(key=lambda item: item.score, reverse=True)
    top = candidates[0] if candidates and candidates[0].score > 0 else None
    runners = candidates[1:] if top else candidates
    return DiscoveryResult(top, runners, list(missing_important or []), list(missing_enrichment or []))

def explain(candidate):
    return candidate.reason_text or candidate.field_key
