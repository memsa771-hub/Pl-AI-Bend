"""Counselor decides PAI's first message. Conversation domain only persists it."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.conversations.service import count_person_messages, save_assistant_message
from pai.domains.student.person.models import Person
from pai.intelligences.counselor.context import build_student_context_pack, compose_opening


async def ensure_thread_opening(
    session: AsyncSession,
    person: Person,
    conversation_id: uuid.UUID,
    *,
    settings=None,
):
    from pai.config import get_settings

    if await count_person_messages(session, person.id):
        return None
    pack = await build_student_context_pack(
        session,
        person,
        conversation_id=conversation_id,
        settings=settings or get_settings(),
    )
    return await save_assistant_message(
        session,
        person,
        conversation_id,
        compose_opening(pack),
        provider="system",
        model="opening.v1",
        update_title=False,
    )
