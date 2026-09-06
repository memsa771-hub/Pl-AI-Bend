from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.goals.models import Goal
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
from pai.domains.student.vault.catalog import CatalogField
from pai.domains.student.vault.completion import apply_completion_to_vault
from pai.domains.student.vault.service import expand_scope_for_person
from pai.kernel.contracts.schemas import VaultCandidate


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
        return {"degree": text}

    if not isinstance(value, dict):
        return None

    out: dict[str, Any] = {}
    institution = value.get("institution")
    degree = value.get("degree") or value.get("program") or value.get("qualification")
    major = value.get("major") or value.get("stream") or value.get("group")

    if institution and str(institution).strip():
        out["institution"] = str(institution).strip()
    if degree is not None and str(degree).strip():
        out["degree"] = str(degree).strip()
    if major is not None and str(major).strip():
        out["major"] = str(major).strip()

    if value.get("gpa") is not None:
        out["gpa"] = float(value["gpa"])
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


async def _find_education_match(
    session: AsyncSession,
    person_id: uuid.UUID,
    payload: dict[str, Any],
) -> Education | None:
    institution = payload.get("institution")
    if institution:
        result = await session.execute(
            select(Education).where(
                Education.person_id == person_id,
                Education.institution == institution,
            )
        )
        hit = result.scalar_one_or_none()
        if hit:
            return hit

    degree = payload.get("degree")
    major = payload.get("major")
    if degree or major:
        result = await session.execute(
            select(Education)
            .where(Education.person_id == person_id)
            .order_by(Education.updated_at.desc())
        )
        for row in result.scalars().all():
            deg_ok = not degree or (row.degree or "").lower() == str(degree).lower()
            maj_ok = not major or (row.major or "").lower() == str(major).lower()
            inst_ok = not institution or (row.institution or "").lower() == str(institution).lower()
            if deg_ok and maj_ok and (inst_ok or not institution):
                if degree or major:
                    return row

    # Bare GPA/percentage correction against single/most-recent education
    if payload.get("gpa") is not None or payload.get("percentage") is not None:
        result = await session.execute(
            select(Education)
            .where(Education.person_id == person_id)
            .order_by(Education.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
    return None


def _apply_education_fields(row: Education, payload: dict[str, Any]) -> None:
    if payload.get("institution") and row.institution != payload["institution"]:
        # Keep established institution unless it was a placeholder equal to degree
        if row.institution in (row.degree, row.major, None, ""):
            row.institution = payload["institution"]
    if payload.get("degree"):
        row.degree = payload["degree"]
    if payload.get("major"):
        row.major = payload["major"]
    if payload.get("gpa") is not None:
        row.gpa = payload["gpa"]
    if payload.get("gpa_scale") is not None:
        row.gpa_scale = payload["gpa_scale"]
    if payload.get("percentage") is not None:
        row.percentage = payload["percentage"]
    if payload.get("graduation_year") is not None:
        row.graduation_year = payload["graduation_year"]
    if payload.get("status"):
        row.status = payload["status"]


def _education_snapshot(row: Education) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "institution": row.institution,
        "degree": row.degree,
        "major": row.major,
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


async def _apply_education_one(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    field: CatalogField,
    *,
    vault_status: str,
    recompute_completion: bool,
) -> TypedApplyResult:
    payload = _education_payload(candidate.value)
    if payload is None and field.key in ("education.gpa", "education.program"):
        if isinstance(candidate.value, (int, float)):
            existing = await _find_education_match(
                session, person.id, {"gpa": float(candidate.value)}
            )
            if existing is None:
                return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)
            payload = {"institution": existing.institution, "gpa": float(candidate.value)}
        elif isinstance(candidate.value, str) and _MARKS_RE.match(candidate.value):
            m = _MARKS_RE.match(candidate.value)
            assert m
            obtained, total = float(m.group(1)), float(m.group(2))
            pct = round(100.0 * obtained / total, 2) if total else None
            existing = await _find_education_match(
                session, person.id, {"percentage": pct or 0}
            )
            if existing is None:
                return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)
            payload = {"institution": existing.institution, "percentage": pct}

    if payload is None:
        return TypedApplyResult(candidate.field_key, "rejected", candidate.confidence)

    existing = await _find_education_match(session, person.id, payload)
    old_snapshot = _education_snapshot(existing) if existing else None
    institution = (payload.get("institution") or "").strip()
    invented = institution in (payload.get("degree"), payload.get("major"))
    if existing:
        if invented:
            payload = {k: v for k, v in payload.items() if k != "institution"}
        _apply_education_fields(existing, payload)
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
        )
        session.add(row)
        await session.flush()
        status = "accepted"
    await expand_scope_for_person(session, person, SCOPE_BY_RESOURCE["educations"])
    await _log_typed_history(
        session,
        person,
        candidate.field_key,
        old_value=old_snapshot,
        new_value=_education_snapshot(row),
        candidate=candidate,
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


async def apply_typed_candidate(
    session: AsyncSession,
    person: Person,
    candidate: VaultCandidate,
    field: CatalogField,
    *,
    vault_status: str,
    recompute_completion: bool = True,
) -> TypedApplyResult:
    if field.storage == "educations":
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
        )

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
