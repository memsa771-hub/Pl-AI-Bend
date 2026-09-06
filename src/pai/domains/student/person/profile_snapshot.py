"""Serialize typed profile rows for counselor context (not just counts)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.goals.models import Goal
from pai.domains.student.person.models import (
    Certification,
    Education,
    Project,
    Skill,
    WorkExperience,
)


def _edu_dict(row: Education) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "institution": row.institution,
        "degree": row.degree,
        "major": row.major,
        "graduationYear": row.graduation_year,
        "gpa": row.gpa,
        "gpaScale": row.gpa_scale,
        "percentage": row.percentage,
        "status": row.status,
        "startDate": row.start_date.isoformat() if row.start_date else None,
        "endDate": row.end_date.isoformat() if row.end_date else None,
    }


def _goal_dict(row: Goal) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "goalType": row.goal_type,
        "title": row.title,
        "description": row.description,
        "status": row.status,
        "priority": row.priority,
    }


def _work_dict(row: WorkExperience) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization": row.organization,
        "title": row.title,
        "employmentType": row.employment_type,
        "isCurrent": row.is_current,
        "description": (row.description or "")[:400] or None,
    }


def _project_dict(row: Project) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "role": row.role,
        "description": (row.description or "")[:400] or None,
    }


def _skill_dict(row: Skill) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "proficiency": row.proficiency,
    }


def _cert_dict(row: Certification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "issuer": row.issuer,
    }


async def load_typed_profile_records(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    limit_per_type: int = 20,
) -> dict[str, Any]:
    """Full typed records for the counselor (contents, not counts)."""
    educations = list(
        (
            await session.execute(
                select(Education)
                .where(Education.person_id == person_id)
                .order_by(Education.updated_at.desc())
                .limit(limit_per_type)
            )
        ).scalars()
    )
    goals = list(
        (
            await session.execute(
                select(Goal)
                .where(Goal.person_id == person_id)
                .order_by(Goal.updated_at.desc())
                .limit(limit_per_type)
            )
        ).scalars()
    )
    work = list(
        (
            await session.execute(
                select(WorkExperience)
                .where(WorkExperience.person_id == person_id)
                .order_by(WorkExperience.updated_at.desc())
                .limit(limit_per_type)
            )
        ).scalars()
    )
    projects = list(
        (
            await session.execute(
                select(Project)
                .where(Project.person_id == person_id)
                .order_by(Project.updated_at.desc())
                .limit(limit_per_type)
            )
        ).scalars()
    )
    skills = list(
        (
            await session.execute(
                select(Skill)
                .where(Skill.person_id == person_id)
                .order_by(Skill.updated_at.desc())
                .limit(limit_per_type)
            )
        ).scalars()
    )
    certs = list(
        (
            await session.execute(
                select(Certification)
                .where(Certification.person_id == person_id)
                .order_by(Certification.updated_at.desc())
                .limit(limit_per_type)
            )
        ).scalars()
    )
    return {
        "educations": [_edu_dict(r) for r in educations],
        "goals": [_goal_dict(r) for r in goals],
        "workExperiences": [_work_dict(r) for r in work],
        "projects": [_project_dict(r) for r in projects],
        "skills": [_skill_dict(r) for r in skills],
        "certifications": [_cert_dict(r) for r in certs],
        "counts": {
            "educations": len(educations),
            "goals": len(goals),
            "workExperiences": len(work),
            "projects": len(projects),
            "skills": len(skills),
            "certifications": len(certs),
        },
    }
