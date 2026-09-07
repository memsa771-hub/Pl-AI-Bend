"""Detection and persistence of profile issues.

A detected issue is not an error (doc §11). Gaps can be legitimate, overlapping
work is normal, and students skip stages. This layer records what looks
suspicious so that discovery can rank it against everything else PAI could ask
about — it never blocks a write and never asks a question by itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.student.person.models import ProfileIssue

# Lifecycle. `accepted_as_valid` and `ignored` are terminal user judgements and
# are never reopened by re-detection.
STATUS_OPEN = "open"
STATUS_RESOLVED = "resolved"
STATUS_IGNORED = "ignored"
STATUS_ACCEPTED = "accepted_as_valid"
TERMINAL_STATUSES = (STATUS_IGNORED, STATUS_ACCEPTED)

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True, slots=True)
class DetectedIssue:
    """A candidate issue produced by a validator, before persistence."""

    domain: str
    issue_type: str
    fingerprint: str
    severity: str = "medium"
    confidence: float = 0.6
    related_entity_type: str | None = None
    related_entity_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    clarification_needed: bool = False
    clarification_prompt: str | None = None


async def list_issues(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    domain: str | None = None,
    status: str | None = STATUS_OPEN,
    limit: int = 50,
) -> list[ProfileIssue]:
    stmt = select(ProfileIssue).where(ProfileIssue.person_id == person_id)
    if domain:
        stmt = stmt.where(ProfileIssue.domain == domain)
    if status:
        stmt = stmt.where(ProfileIssue.status == status)
    stmt = stmt.order_by(ProfileIssue.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def record_issue(
    session: AsyncSession,
    person_id: uuid.UUID,
    detected: DetectedIssue,
    *,
    detected_from: str,
) -> ProfileIssue | None:
    """Upsert one issue by fingerprint. Returns None if the user settled it already."""
    existing = (
        await session.execute(
            select(ProfileIssue).where(
                ProfileIssue.person_id == person_id,
                ProfileIssue.fingerprint == detected.fingerprint,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        if existing.status in TERMINAL_STATUSES:
            return None
        existing.status = STATUS_OPEN
        existing.resolved_at = None
        existing.severity = detected.severity
        existing.confidence = detected.confidence
        existing.related_entity_ids = detected.related_entity_ids
        existing.detail = detected.detail
        existing.clarification_needed = detected.clarification_needed
        existing.clarification_prompt = detected.clarification_prompt
        existing.detected_from = detected_from
        return existing

    row = ProfileIssue(
        person_id=person_id,
        domain=detected.domain,
        issue_type=detected.issue_type,
        fingerprint=detected.fingerprint,
        severity=detected.severity,
        confidence=detected.confidence,
        related_entity_type=detected.related_entity_type,
        related_entity_ids=detected.related_entity_ids,
        detected_from=detected_from,
        detail=detected.detail,
        clarification_needed=detected.clarification_needed,
        clarification_prompt=detected.clarification_prompt,
        status=STATUS_OPEN,
    )
    session.add(row)
    await session.flush()
    return row


async def sync_issues(
    session: AsyncSession,
    person_id: uuid.UUID,
    detected: list[DetectedIssue],
    *,
    domain: str,
    authoritative_types: frozenset[str],
    detected_from: str,
) -> list[ProfileIssue]:
    """Reconcile a full re-scan of one domain against what is already stored.

    `authoritative_types` scopes auto-resolution to the issue types this scan
    actually looks for, so a timeline re-scan never silently closes a
    write-time contradiction it knows nothing about.
    """
    saved: list[ProfileIssue] = []
    for item in detected:
        row = await record_issue(session, person_id, item, detected_from=detected_from)
        if row is not None:
            saved.append(row)

    still_present = {item.fingerprint for item in detected}
    open_rows = (
        await session.execute(
            select(ProfileIssue).where(
                ProfileIssue.person_id == person_id,
                ProfileIssue.domain == domain,
                ProfileIssue.status == STATUS_OPEN,
                ProfileIssue.issue_type.in_(tuple(authoritative_types)),
            )
        )
    ).scalars().all()
    for row in open_rows:
        if row.fingerprint not in still_present:
            row.status = STATUS_RESOLVED
            row.resolved_at = datetime.now(UTC)
            row.resolution = {"reason": "no_longer_detected", "by": detected_from}
    return saved


async def resolve_issue(
    session: AsyncSession,
    person_id: uuid.UUID,
    issue_id: uuid.UUID,
    *,
    status: str,
    resolution: dict[str, Any] | None = None,
) -> ProfileIssue | None:
    if status not in (STATUS_RESOLVED, STATUS_IGNORED, STATUS_ACCEPTED, STATUS_OPEN):
        raise ValueError(f"Unsupported issue status: {status}")
    row = (
        await session.execute(
            select(ProfileIssue).where(
                ProfileIssue.id == issue_id, ProfileIssue.person_id == person_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    row.status = status
    row.resolution = resolution
    row.resolved_at = None if status == STATUS_OPEN else datetime.now(UTC)
    return row


def issue_dict(row: ProfileIssue) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "domain": row.domain,
        "issueType": row.issue_type,
        "severity": row.severity,
        "confidence": row.confidence,
        "relatedEntityType": row.related_entity_type,
        "relatedEntityIds": list(row.related_entity_ids or []),
        "detectedFrom": row.detected_from,
        "detail": dict(row.detail or {}),
        "clarificationNeeded": row.clarification_needed,
        "clarificationPrompt": row.clarification_prompt,
        "status": row.status,
        "resolution": row.resolution,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
    }
