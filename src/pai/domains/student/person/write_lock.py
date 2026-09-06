"""Serialize owned canonical writes and fence deleted accounts."""
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pai.domains.student.person.models import Person
from pai.kernel.errors import PersonNotFoundError

async def lock_person(session, person_id):
    person = (await session.execute(select(Person).options(selectinload(Person.vault)).where(
        Person.id == person_id, Person.deleted_at.is_(None)
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if person is None:
        raise PersonNotFoundError()
    return person
