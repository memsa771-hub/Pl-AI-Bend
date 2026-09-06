from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.kernel.errors import PersonNotFoundError
from pai.domains.goals.models import Goal
from pai.domains.student.person.models import (
    Certification,
    Education,
    Person,
    Project,
    Skill,
    WorkExperience,
)
from pai.domains.student.vault.completion import apply_completion_to_vault
from pai.domains.student.vault.service import expand_scope_for_person

SCOPE_BY_RESOURCE = {
    "educations": "education",
    "work_experiences": "career",
    "projects": "career",
    "skills": "career",
    "certifications": "career",
    "goals": "application",
}


async def list_resources(
    session: AsyncSession,
    model: type,
    person_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[Any]:
    result = await session.execute(
        select(model).where(model.person_id == person_id).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def create_resource(
    session: AsyncSession,
    model: type,
    person: Person,
    data: dict[str, Any],
) -> Any:
    row = model(person_id=person.id, **data)
    session.add(row)
    await session.flush()
    scope = SCOPE_BY_RESOURCE.get(model.__tablename__)
    if scope:
        await expand_scope_for_person(session, person, scope)
    if model is Goal and data.get("goal_type", "").lower() in (
        "relocation",
        "mobility",
        "relocate",
    ):
        await expand_scope_for_person(session, person, "mobility")
    if person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    await session.commit()
    return row


async def update_resource(
    session: AsyncSession,
    model: type,
    person: Person,
    resource_id: uuid.UUID,
    data: dict[str, Any],
) -> Any:
    result = await session.execute(
        select(model).where(model.id == resource_id, model.person_id == person.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise PersonNotFoundError("Resource not found.")
    for key, val in data.items():
        if hasattr(row, key) and val is not None:
            setattr(row, key, val)
    await session.flush()
    if person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    await session.commit()
    return row


async def delete_resource(
    session: AsyncSession,
    model: type,
    person: Person,
    resource_id: uuid.UUID,
) -> None:
    result = await session.execute(
        select(model).where(model.id == resource_id, model.person_id == person.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise PersonNotFoundError("Resource not found.")
    await session.delete(row)
    if person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    await session.commit()


MODELS = {
    "educations": Education,
    "work-experiences": WorkExperience,
    "projects": Project,
    "skills": Skill,
    "certifications": Certification,
    "goals": Goal,
}
