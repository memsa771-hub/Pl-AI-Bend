from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.goals.models import Goal
from pai.domains.student.education.resolver import ResolutionOutcome, resolve_education
from pai.domains.student.education.timeline import (
    TIMELINE_ISSUE_TYPES,
    validate_education_timeline,
)
from pai.domains.student.evidence import (
    DEFAULT_VERIFICATION,
    attribute_verification,
    record_entity_evidence,
    verification_rank,
)
from pai.domains.student.issues.service import DetectedIssue, record_issue, sync_issues
from pai.domains.student.normalization.phone import normalize_phone
from pai.domains.student.person.models import (
    Certification,
    Education,
    Person,
    Project,
    Skill,
    VaultHistory,
    WorkExperience,
)
from pai.domains.student.person.typed_resources import SCOPE_BY_RESOURCE
from pai.domains.student.test_attempts import (
    ENTITY_TYPE as TEST_ENTITY,
)
from pai.domains.student.test_attempts import (
    parse_test_observation,
    upsert_test_attempt,
)
from pai.domains.student.vault.catalog import CatalogField
from pai.domains.student.vault.completion import apply_completion_to_vault
from pai.domains.student.vault.service import expand_scope_for_person
from pai.kernel.contracts.schemas import VaultCandidate

EDUCATION_ENTITY = "education"


class TypedApplyResult:
    __slots__ = ("field_key", "status", "confidence")

    def __init__(self, field_key: str, status: str, confidence: float) -> None:
        self.field_key = field_key
        self.status = status
        self.confidence = confidence


_MARKS_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")


def _education_payload(value: Any) -> dict[str, Any] | None:
    """Normalize education candidate values; never invent institution names."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        from pai.domains.student.person.qualifications import qualification_metadata
        return {"degree": text, "qualification_data": qualification_metadata({}, degree=text)}

    if not isinstance(value, dict):
        return None

    out: dict[str, Any] = {"qualification_data": dict(value)}
    if value.get("id"):
        out["id"] = str(value["id"])
    institution = value.get("institution")
    degree = value.get("degree") or value.get("program") or value.get("qualification")
    major = value.get("major") or value.get("stream") or value.get("group")
    from pai.domains.student.person.qualifications import qualification_metadata
    out["qualification_data"] = qualification_metadata(value, degree=degree, field=major)

    if institution and str(institution).strip():
        out["institution"] = str(institution).strip()
    if degree is not None and str(degree).strip():
        out["degree"] = str(degree).strip()
    if major is not None and str(major).strip():
        out["major"] = str(major).strip()

    from pai.domains.student.normalization.grades import finite_number
    if value.get("gpa") is not None:
        number = finite_number(value["gpa"])
        if number is None:
            return None
        out["gpa"] = number
    elif value.get("value") is not None and not institution:
        try:
            out["gpa"] = float(value["value"])
        except (TypeError, ValueError):
            pass
    if value.get("gpa_scale") is not None:
        out["gpa_scale"] = float(value["gpa_scale"])
    elif value.get("scale") is not None and "gpa" in out and not institution:
        try:
            out["gpa_scale"] = float(value["scale"])
        except (TypeError, ValueError):
            pass
    if value.get("graduation_year") is not None:
        out["graduation_year"] = int(value["graduation_year"])
    if value.get("status") is not None:
        out["status"] = str(value["status"])

    marks_obtained = value.get("marks_obtained") or value.get("obtained")
    marks_total = value.get("marks_total") or value.get("total")
    marks = value.get("marks")
    if marks is not None and marks_obtained is None:
        if isinstance(marks, str) and _MARKS_RE.match(marks):
            m = _MARKS_RE.match(marks)
            assert m
            marks_obtained, marks_total = float(m.group(1)), float(m.group(2))
        elif isinstance(marks, dict):
            marks_obtained = marks.get("obtained") or marks.get("marks_obtained")
            marks_total = marks.get("total") or marks.get("marks_total")

    if marks_obtained is not None and marks_total is not None:
        obtained = float(marks_obtained)
        total = float(marks_total)
        if total > 0:
            out["percentage"] = round(100.0 * obtained / total, 2)
            # Keep raw marks in status-free side channel via description fields on row
            out["_marks_obtained"] = obtained
            out["_marks_total"] = total
    elif value.get("percentage") is not None:
        out["percentage"] = float(value["percentage"])

    # Need at least one identifying academic signal
    if not any(k in out for k in ("institution", "degree", "major", "gpa", "percentage")):
        return None
    return out


async def _load_educations(session: AsyncSession, person_id: uuid.UUID) -> list[Education]:
    result = await session.execute(
        select(Education)
        .where(Education.person_id == person_id)
        .order_by(Education.updated_at.desc())
    )
    return list(result.scalars().all())


# Attributes whose overwrite must respect evidence strength rather than recency.
_GUARDED_EDUCATION_ATTRS = (
    "institution",
    "degree",
    "major",
    "gpa",
    "percentage",
    "graduation_year",
)

_CONTRADICTION_TYPE = {
    "gpa": "contradicting_grade",
    "percentage": "contradicting_grade",
    "graduation_year": "contradicting_date",
    "institution": "contradicting_institution",
}


def _apply_qualification_identity(row: Education, payload: dict[str, Any]) -> None:
    """Store only explicit, evidence-backed qualification metadata."""
    if not row.original_name and payload.get("degree"):
        row.original_name = str(payload["degree"])[:256]
    metadata = payload.get("qualification_data") or {}
    level = metadata.get("canonicalLevel") or metadata.get("canonical_level")
    if level in {"secondary", "higher_secondary", "bachelor", "master", "phd"}:
        row.canonical_level = level
    if metadata.get("framework") and not row.framework:
        row.framework = str(metadata["framework"])
    if metadata.get("country") and not row.country:
        row.country = str(metadata["country"])
    row.qualification_data = {**(row.qualification_data or {}), **metadata}


def _contradiction_issue(
    row: Education,
    attribute: str,
    existing_value: Any,
    new_value: Any,
    existing_level: str,
    new_level: str,
) -> DetectedIssue:
    issue_type = _CONTRADICTION_TYPE.get(attribute, "contradicting_value")
    label = attribute.replace("_", " ")
    return DetectedIssue(
        domain="education",
        issue_type=issue_type,
        fingerprint=f"education:{issue_type}:{row.id}:{attribute}",
        severity="high" if attribute in ("gpa", "percentage") else "medium",
        confidence=0.8,
        related_entity_type="education",
        related_entity_ids=[str(row.id)],
        detail={
            "attribute": attribute,
            "existing": existing_value,
            "existingVerification": existing_level,
            "claimed": new_value,
            "claimedVerification": new_level,
            "institution": row.institution,
        },
        clarification_needed=True,
        clarification_prompt=(
            f"their {label} for {row.institution} is on file as {existing_value} "
            f"({existing_level.replace('_', ' ')}) but was just given as {new_value} — "
            "ask which is correct rather than overwriting"
        ),
    )


async def _apply_education_fields(
    session: AsyncSession,
    row: Education,
    payload: dict[str, Any],
    candidate: VaultCandidate,
    *,
    verification_level: str,
) -> tuple[list[str], list[DetectedIssue]]:
    """Write observed attributes, deferring to stronger existing evidence.

    A conflicting claim is never silently dropped or silently applied: the
    weaker one loses the write and is recorded as a contradiction so it can be
    asked about (doc §13).
    """
    strongest = await attribute_verification(
        session, EDUCATION_ENTITY, row.id, list(_GUARDED_EDUCATION_ATTRS)
    )
    written: list[str] = []
    conflicts: list[DetectedIssue] = []

    for attribute in ("institution", "degree", "major", "gpa", "percentage", "graduation_year"):
        if payload.get(attribute) is None:
            continue
        new_value = payload[attribute]
        current = getattr(row, attribute)
        if current == new_value:
            continue
        if current not in (None, ""):
            existing_level = strongest.get(attribute, DEFAULT_VERIFICATION)
            stronger_or_equal = verification_rank(verification_level) >= verification_rank(
                existing_level
            )
            if not (candidate.is_correction or stronger_or_equal):
                conflicts.append(
                    _contradiction_issue(
                        row, attribute, current, new_value, existing_level, verification_level
                    )
                )
                continue
        setattr(row, attribute, new_value)
        written.append(attribute)

    # Unguarded attributes: no prior claim to contradict.
    if payload.get("gpa_scale") is not None and row.gpa_scale != payload["gpa_scale"]:
        row.gpa_scale = payload["gpa_scale"]
        written.append("gpa_scale")
    if payload.get("status") and row.status != payload["status"]:
        row.status = payload["status"]
        written.append("status")

    _apply_qualification_identity(row, payload)
    for attribute in written:
        record_entity_evidence(
            session,
            entity_type=EDUCATION_ENTITY,
            entity_id=row.id,
            attribute=attribute,
            candidate=candidate,
            verification_level=verification_level,
        )
    return written, conflicts


def _education_snapshot(row: Education) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "institution": row.institution,
        "degree": row.degree,
        "major": row.major,
        "canonicalLevel": row.canonical_level,
        "qualificationData": row.qualification_data,
        "gpa": row.gpa,
        "percentage": row.percentage,
        "graduation_year": row.graduation_year,
    }


async def _log_typed_history(
    session: AsyncSession,
    person: Person,
    field_key: str,
    *,
    old_value: Any,
    new_value: Any,
    candidate: VaultCandidate,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> None:
    if person.vault is None:
        return
    session.add(
        VaultHistory(
            vault_id=person.vault.id,
            field_key=field_key,
            action="updated" if old_value is not None else "created",
            old_value=old_value,
            new_value=new_value,
            actor_type="system",
            actor_id=str(person.id),
            entity_type=entity_type,
            entity_id=entity_id,
            reason=(
                f"{candidate.source_type}:{candidate.source_reference}:"
                f"{(candidate.rationale_summary or '')[:180]}"
            ),
        )
    )


async def _upsert_career_goal(
    session: AsyncSession,
    person: Person,
    title: str,
    *,
    vault_status: str,
) -> tuple[Goal, str, dict[str, Any] | None]:
    """Keep one canonical career goal; update instead of duplicating."""
    normalized = title.strip().lower()
    result = await session.execute(
        select(Goal)
        .where(Goal.person_id == person.id)
        .order_by(Goal.updated_at.desc())
    )
    rows = list(result.scalars().all())
    for row in rows:
        if row.title.strip().lower() == normalized:
            return row, "reinforced", _goal_snap(row)
        # Soft match: same program acronym inside title (BSCS / BS CS)
        if normalized in row.title.lower() or row.title.lower() in normalized:
            old = _goal_snap(row)
            row.title = title[:256]
            row.status = "active" if vault_status != "pending" else row.status
            return row, "updated", old

    if rows:
        # Single career objective: update the newest rather than spawn duplicates
        row = rows[0]
        old = _goal_snap(row)
        row.title = title[:256]
        row.status = "active" if vault_status != "pending" else "proposed"
        return row, "updated", old

    from pai.domains.goals.service import (
        LIFECYCLE_ACTIVE,
        LIFECYCLE_DRAFT,
        activate_goal,
        create_goal,
    )
    from pai.domains.goals.types import GoalType

    status = LIFECYCLE_ACTIVE if vault_status != "pending" else LIFECYCLE_DRAFT
    goal = await create_goal(
        session,
        person.id,
        title=title[:256],
        goal_type=GoalType.GENERAL.value,
        anchors={},
        lifecycle_status=status,
    )
    if status == LIFECYCLE_ACTIVE:
        await activate_goal(session, goal)
    return goal, "accepted", None


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "", [], {})]
    return [value]


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 7 and text[4] == "-":
        text = f"{text}-01"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _goal_snap(row: Goal) -> dict[str, Any]:
    return {"id": str(row.id), "title": row.title, "status": row.status}


async def _ambiguity_issue(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    outcome: ResolutionOutcome,
    payload: dict[str, Any],
) -> None:
    """Record that an observation could describe more than one record (doc §10)."""
    rival_ids = sorted(str(row.id) for row in outcome.rivals)
    fingerprint_parts = rival_ids or [candidate.field_key]
    described = ", ".join(
        str(payload.get(key)) for key in ("gpa", "percentage", "degree") if payload.get(key)
    )
    await record_issue(
        session,
        person.id,
        DetectedIssue(
            domain="education",
            issue_type="ambiguous_entity",
            fingerprint="education:ambiguous_entity:" + ":".join(fingerprint_parts),
            severity="medium",
            confidence=0.75,
            related_entity_type="education",
            related_entity_ids=rival_ids,
            detail={
                "observation": {k: v for k, v in payload.items() if not k.startswith("_")},
                "reason": outcome.reason,
                "evidenceText": candidate.evidence_text,
            },
            clarification_needed=True,
            clarification_prompt=(
                f"they mentioned {described or 'an academic detail'} but it could belong to "
                "more than one of their qualifications — ask which one it refers to"
            ),
        ),
        detected_from=f"{candidate.source_type}:education_write",
    )


async def revalidate_education_timeline(
    session: AsyncSession,
    person: Person,
    *,
    detected_from: str,
) -> None:
    """Re-derive timeline issues from the student's full education history."""
    rows = await _load_educations(session, person.id)
    await sync_issues(
        session,
        person.id,
        validate_education_timeline(rows),
        domain="education",
        authoritative_types=TIMELINE_ISSUE_TYPES,
        detected_from=detected_from,
    )


async def _apply_education_one(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    field: CatalogField,
    *,
    vault_status: str,
    recompute_completion: bool,
    verification_level: str = DEFAULT_VERIFICATION,
    validate_timeline: bool = True,
) -> TypedApplyResult:
    payload = _education_payload(candidate.value)
    if payload is None and field.key in ("education.gpa", "education.program"):
        # A bare grade with no qualification named. The resolver decides whether
        # the student has exactly one record it could belong to.
        if isinstance(candidate.value, (int, float)):
            payload = {"gpa": float(candidate.value)}
        elif isinstance(candidate.value, str) and _MARKS_RE.match(candidate.value):
            m = _MARKS_RE.match(candidate.value)
            assert m
            obtained, total = float(m.group(1)), float(m.group(2))
            payload = {"percentage": round(100.0 * obtained / total, 2)} if total else None

    if payload is None:
        return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)

    institution = (payload.get("institution") or "").strip()
    invented = bool(institution) and institution in (payload.get("degree"), payload.get("major"))

    rows = await _load_educations(session, person.id)
    outcome = resolve_education(payload, rows)
    if outcome.ambiguous:
        await _ambiguity_issue(session, person, candidate, outcome, payload)
        return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)

    existing = outcome.match
    old_snapshot = _education_snapshot(existing) if existing else None
    conflicts: list[DetectedIssue] = []
    if existing is not None:
        if invented:
            payload = {k: v for k, v in payload.items() if k != "institution"}
        _written, conflicts = await _apply_education_fields(
            session, existing, payload, candidate, verification_level=verification_level
        )
        row = existing
        status = "updated"
    elif not institution or invented:
        return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)
    else:
        row = Education(
            person_id=person.id,
            institution=payload["institution"],
            degree=payload.get("degree"),
            major=payload.get("major"),
            gpa=payload.get("gpa"),
            gpa_scale=payload.get("gpa_scale"),
            percentage=payload.get("percentage"),
            graduation_year=payload.get("graduation_year"),
            status=payload.get("status") or "completed",
            qualification_data=payload.get("qualification_data"),
        )
        _apply_qualification_identity(row, payload)
        session.add(row)
        await session.flush()
        for attribute in ("institution", "degree", "major", "gpa", "percentage", "graduation_year"):
            if payload.get(attribute) is not None:
                record_entity_evidence(
                    session,
                    entity_type=EDUCATION_ENTITY,
                    entity_id=row.id,
                    attribute=attribute,
                    candidate=candidate,
                    verification_level=verification_level,
                )
        status = "accepted"

    for conflict in conflicts:
        await record_issue(
            session,
            person.id,
            conflict,
            detected_from=f"{candidate.source_type}:education_write",
        )

    await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["educations"])
    await _log_typed_history(
        session,
        person,
        candidate.field_key,
        old_value=old_snapshot,
        new_value=_education_snapshot(row),
        candidate=candidate,
        entity_type=EDUCATION_ENTITY,
        entity_id=row.id,
    )
    if validate_timeline:
        await revalidate_education_timeline(
            session, person, detected_from=f"{candidate.source_type}:education_write"
        )
    if recompute_completion and person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    out = "pending" if vault_status == "pending" else status
    return TypedApplyResult(candidate.field_key, out, candidate.confidence)


async def _upsert_skills(
    session: AsyncSession, person: Person, items: list[Any], candidate: VaultCandidate
) -> str:
    existing = await session.execute(select(Skill).where(Skill.person_id == person.id))
    known = {row.name.strip().lower(): row for row in existing.scalars() if row.name}
    status = "reinforced"
    added: list[str] = []
    for raw in items:
        if isinstance(raw, str):
            name, proficiency = raw.strip(), None
        elif isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("skill") or "").strip()
            proficiency = raw.get("proficiency")
            proficiency = str(proficiency).strip() if proficiency else None
        else:
            continue
        if not name:
            continue
        key = name.lower()
        if key in known:
            if proficiency and not known[key].proficiency:
                known[key].proficiency = proficiency[:64]
                status = "updated"
            continue
        row = Skill(person_id=person.id, name=name[:128], proficiency=proficiency)
        session.add(row)
        known[key] = row
        added.append(name)
        status = "accepted"
    if added or status != "reinforced":
        await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["skills"])
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=None,
            new_value=added or "updated",
            candidate=candidate,
        )
    return status if (added or status != "reinforced") else "rejected"


async def _upsert_work(
    session: AsyncSession, person: Person, items: list[Any], candidate: VaultCandidate
) -> str:
    existing = await session.execute(
        select(WorkExperience).where(WorkExperience.person_id == person.id)
    )
    known = {
        (row.organization.strip().lower(), row.title.strip().lower()): row
        for row in existing.scalars()
        if row.organization and row.title
    }
    status = "rejected"
    for raw in items:
        if not isinstance(raw, dict):
            continue
        org = str(raw.get("organization") or raw.get("company") or "").strip()
        title = str(raw.get("title") or raw.get("role") or "").strip()
        if not org or not title:
            continue
        key = (org.lower(), title.lower())
        desc = raw.get("description")
        emp = raw.get("employment_type") or raw.get("employmentType")
        current = bool(raw.get("is_current") or raw.get("isCurrent") or raw.get("current"))
        start = _parse_date(raw.get("start_date") or raw.get("startDate"))
        end = _parse_date(raw.get("end_date") or raw.get("endDate"))
        if key in known:
            row = known[key]
            if desc and not row.description:
                row.description = str(desc)
            if emp and not row.employment_type:
                row.employment_type = str(emp)[:64]
            row.is_current = current or row.is_current
            if start and not row.start_date:
                row.start_date = start
            if end and not row.end_date:
                row.end_date = end
            status = "updated" if status != "accepted" else status
            continue
        row = WorkExperience(
            person_id=person.id,
            organization=org[:256],
            title=title[:256],
            employment_type=str(emp)[:64] if emp else None,
            is_current=current,
            description=str(desc) if desc else None,
            start_date=start,
            end_date=end,
        )
        session.add(row)
        known[key] = row
        status = "accepted"
    if status != "rejected":
        await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["work_experiences"])
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=None,
            new_value=status,
            candidate=candidate,
        )
    return status


async def _upsert_projects(
    session: AsyncSession, person: Person, items: list[Any], candidate: VaultCandidate
) -> str:
    existing = await session.execute(select(Project).where(Project.person_id == person.id))
    known = {row.name.strip().lower(): row for row in existing.scalars() if row.name}
    status = "rejected"
    for raw in items:
        if isinstance(raw, str):
            name, role, desc, url = raw.strip(), None, None, None
        elif isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("title") or "").strip()
            role = raw.get("role")
            desc = raw.get("description")
            url = raw.get("url")
        else:
            continue
        if not name:
            continue
        key = name.lower()
        if key in known:
            row = known[key]
            if role and not row.role:
                row.role = str(role)[:128]
            if desc and not row.description:
                row.description = str(desc)
            if url and not row.url:
                row.url = str(url)[:512]
            status = "updated" if status != "accepted" else status
            continue
        row = Project(
            person_id=person.id,
            name=name[:256],
            role=str(role)[:128] if role else None,
            description=str(desc) if desc else None,
            url=str(url)[:512] if url else None,
        )
        session.add(row)
        known[key] = row
        status = "accepted"
    if status != "rejected":
        await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["projects"])
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=None,
            new_value=status,
            candidate=candidate,
        )
    return status


async def _upsert_certs(
    session: AsyncSession, person: Person, items: list[Any], candidate: VaultCandidate
) -> str:
    existing = await session.execute(
        select(Certification).where(Certification.person_id == person.id)
    )
    known = {row.name.strip().lower(): row for row in existing.scalars() if row.name}
    status = "rejected"
    for raw in items:
        if isinstance(raw, str):
            name, issuer = raw.strip(), None
        elif isinstance(raw, dict):
            name = str(raw.get("name") or raw.get("title") or "").strip()
            issuer = raw.get("issuer")
        else:
            continue
        if not name:
            continue
        key = name.lower()
        if key in known:
            if issuer and not known[key].issuer:
                known[key].issuer = str(issuer)[:256]
                status = "updated" if status != "accepted" else status
            continue
        row = Certification(
            person_id=person.id,
            name=name[:256],
            issuer=str(issuer)[:256] if issuer else None,
        )
        session.add(row)
        known[key] = row
        status = "accepted"
    if status != "rejected":
        await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["certifications"])
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=None,
            new_value=status,
            candidate=candidate,
        )
    return status


def _person_value(field_key: str, value: Any) -> Any:
    if field_key == "identity.phone" and isinstance(value, str):
        try:
            return normalize_phone(value)
        except ValueError:
            return value.strip()
    if field_key in {"identity.full_name", "identity.preferred_name"} and isinstance(value, str):
        return value.strip()[:256]
    return value


async def _apply_test_scores(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    *,
    verification_level: str,
) -> str:
    status = "rejected"
    for raw in _as_items(candidate.value):
        observation = parse_test_observation(raw)
        if observation is None:
            continue
        row, outcome = await upsert_test_attempt(session, person.id, observation)
        record_entity_evidence(
            session,
            entity_type=TEST_ENTITY,
            entity_id=row.id,
            attribute="overall_score",
            candidate=candidate,
            verification_level=verification_level,
        )
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=None,
            new_value={
                "testType": row.test_type,
                "attemptNumber": row.attempt_number,
                "overallScore": row.overall_score,
            },
            candidate=candidate,
            entity_type=TEST_ENTITY,
            entity_id=row.id,
        )
        if outcome == "accepted" or status == "rejected":
            status = outcome
    return status


async def apply_typed_candidate(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    field: CatalogField,
    *,
    vault_status: str,
    recompute_completion: bool = True,
    verification_level: str = DEFAULT_VERIFICATION,
) -> TypedApplyResult:
    if field.storage == "educations":
        if vault_status == "pending":
            from pai.kernel.evidence.vault_apply import apply_vault_candidate
            result = await apply_vault_candidate(
                session, person, candidate, vault_status="pending",
                verification_level=verification_level,
                recompute_completion=recompute_completion,
            )
            return TypedApplyResult(result.field_key, result.status, result.confidence)
        items = _as_items(candidate.value) if isinstance(candidate.value, list) else None
        if items:
            last = TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)
            for item in items:
                piece = candidate.model_copy(update={"value": item})
                last = await _apply_education_one(
                    session,
                    person,
                    piece,
                    field,
                    vault_status=vault_status,
                    recompute_completion=False,
                    verification_level=verification_level,
                    validate_timeline=False,
                )
            # One validation pass over the finished timeline, not one per row.
            await revalidate_education_timeline(
                session, person, detected_from=f"{candidate.source_type}:education_write"
            )
            if recompute_completion and person.vault:
                await apply_completion_to_vault(session, person, person.vault)
            return last
        return await _apply_education_one(
            session,
            person,
            candidate,
            field,
            vault_status=vault_status,
            recompute_completion=recompute_completion,
            verification_level=verification_level,
        )

    if field.storage == "test_attempts":
        status = await _apply_test_scores(
            session, person, candidate, verification_level=verification_level
        )
        if recompute_completion and person.vault and status != "rejected":
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" and status != "rejected" else status
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    if field.storage == "goals" and field.key == "application.career_interest":
        title = candidate.value if isinstance(candidate.value, str) else str(candidate.value)
        # Delegate to GoalService so multi-goal logic is respected
        try:
            from pai.domains.goals.service import (
                INTEL_PENDING,
                INTEL_STALE,
                enqueue_goal_intelligence_job,
                upsert_goal_from_anchors,
            )
            from pai.domains.goals.types import GoalType, GoalWriteAction

            goal, action = await upsert_goal_from_anchors(
                session,
                person.id,
                goal_type=GoalType.GENERAL.value,
                title=title,
                anchors={"title": title[:256]},
                activate=(vault_status != "pending"),
                create_if_new=True,
            )
            if goal is not None and (
                action != GoalWriteAction.REINFORCE
                or goal.intelligence_status in (INTEL_PENDING, INTEL_STALE)
            ):
                await enqueue_goal_intelligence_job(session, goal)
            old = _goal_snap(goal) if goal is not None else None
            status = action
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("GoalService upsert failed; falling back to legacy")
            goal, status, old = await _upsert_career_goal(
                session, person, title, vault_status=vault_status
            )
        await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["goals"])
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=old,
            new_value=_goal_snap(goal),
            candidate=candidate,
        )
        if recompute_completion and person.vault:
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" else status
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    if field.storage == "skills":
        status = await _upsert_skills(session, person, _as_items(candidate.value), candidate)
        if recompute_completion and person.vault and status != "rejected":
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" and status != "rejected" else status
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    if field.storage == "work_experiences":
        status = await _upsert_work(session, person, _as_items(candidate.value), candidate)
        if recompute_completion and person.vault and status != "rejected":
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" and status != "rejected" else status
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    if field.storage == "projects":
        status = await _upsert_projects(session, person, _as_items(candidate.value), candidate)
        if recompute_completion and person.vault and status != "rejected":
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" and status != "rejected" else status
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    if field.storage == "certifications":
        status = await _upsert_certs(session, person, _as_items(candidate.value), candidate)
        if recompute_completion and person.vault and status != "rejected":
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" and status != "rejected" else status
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    if field.storage == "person" and field.person_column:
        value = _person_value(candidate.field_key, candidate.value)
        old = getattr(person, field.person_column, None)
        setattr(person, field.person_column, value)
        await _log_typed_history(
            session,
            person,
            candidate.field_key,
            old_value=old,
            new_value=value,
            candidate=candidate,
        )
        if recompute_completion and person.vault:
            await apply_completion_to_vault(session, person, person.vault)
        out = "pending" if vault_status == "pending" else "accepted"
        return TypedApplyResult(candidate.field_key, out, candidate.confidence)

    return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)
