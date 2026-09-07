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
    "test_attempts": "application",
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


async def _sync_education_derivations(
    session: AsyncSession, person: Person, row: Any
) -> None:
    """Keep manually edited education consistent with extracted education.

    A degree typed into the UI has to land on the same canonical level and be
    validated the same way as one PAI learned from chat, otherwise the timeline
    only understands half the student's history.
    """
    from pai.domains.student.typed_apply import (
        _apply_qualification_identity,
        revalidate_education_timeline,
    )

    _apply_qualification_identity(
        row,
        {"degree": row.degree, "major": row.major, "original_name": row.original_name},
    )
    await session.flush()
    await revalidate_education_timeline(session, person, detected_from="manual:education_edit")


async def create_resource(
    session: AsyncSession,
    model: type,
    person: Person,
    data: dict[str, Any],
) -> Any:
    from pai.domains.student.person.write_lock import lock_person
    await lock_person(session, person.id)
    row = model(person_id=person.id, **data)
    session.add(row)
    await session.flush()
    if model is Education:
        await _sync_education_derivations(session, person, row)
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
    from pai.domains.goals.service import mark_intelligence_stale_for_vault_update
    await mark_intelligence_stale_for_vault_update(session, person.id, model.__tablename__)
    await session.commit()
    await session.refresh(row)
    return row


async def update_resource(
    session: AsyncSession,
    model: type,
    person: Person,
    resource_id: uuid.UUID,
    data: dict[str, Any],
) -> Any:
    from pai.domains.student.person.write_lock import lock_person
    await lock_person(session, person.id)
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
    if model is Education:
        await _sync_education_derivations(session, person, row)
    if person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    from pai.domains.goals.service import mark_intelligence_stale_for_vault_update
    await mark_intelligence_stale_for_vault_update(session, person.id, model.__tablename__)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_resource(
    session: AsyncSession,
    model: type,
    person: Person,
    resource_id: uuid.UUID,
) -> None:
    from pai.domains.student.person.write_lock import lock_person
    await lock_person(session, person.id)
    result = await session.execute(
        select(model).where(model.id == resource_id, model.person_id == person.id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise PersonNotFoundError("Resource not found.")
    await session.delete(row)
    await session.flush()
    if model is Education:
        from pai.domains.student.typed_apply import revalidate_education_timeline

        await revalidate_education_timeline(
            session, person, detected_from="manual:education_delete"
        )
    if person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    from pai.domains.goals.service import mark_intelligence_stale_for_vault_update
    await mark_intelligence_stale_for_vault_update(session, person.id, model.__tablename__)
    await session.commit()


MODELS = {
    "educations": Education,
    "work-experiences": WorkExperience,
    "projects": Project,
    "skills": Skill,
    "certifications": Certification,
    "goals": Goal,
}
