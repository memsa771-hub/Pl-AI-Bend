from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.conversations.models import Conversation, Message, OrchestrationRun
from pai.kernel.errors import AuthError
from pai.domains.student.person.models import Person


class ConversationNotFoundError(AuthError):
    def __init__(self) -> None:
        super().__init__(code="CONVERSATION_NOT_FOUND", message="Conversation not found.", status_code=404)


async def create_conversation(
    session: AsyncSession,
    person: Person,
    *,
    settings=None,
) -> Conversation:
    row = Conversation(person_id=person.id, title="PAI")
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def get_latest_active_conversation(
    session: AsyncSession, person_id: uuid.UUID
) -> Conversation | None:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.person_id == person_id, Conversation.status == "active")
        .order_by(Conversation.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_or_create_person_conversation(
    session: AsyncSession,
    person: Person,
    *,
    settings=None,
) -> Conversation:
    """One counselor transcript per person. Extra rows from older clients are ignored for writes."""
    existing = await get_latest_active_conversation(session, person.id)
    if existing is not None:
        return existing
    return await create_conversation(session, person, settings=settings)


async def get_conversation_owned(
    session: AsyncSession, person_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.person_id == person_id,
            Conversation.status != "deleted",
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ConversationNotFoundError()
    return row


async def list_person_messages(
    session: AsyncSession,
    person: Person,
    *,
    settings=None,
    limit: int = 50,
    offset: int | None = None,
) -> tuple[list[Message], int, int, Conversation]:
    """Paginated history for this person (all threads, chronological).

    Omit offset to return the latest `limit` messages (chat window). Pass offset
    from 0 to walk older pages. Extra conversation rows from older clients are
    included so the UI stays one merged PAI transcript.
    """
    conv = await get_or_create_person_conversation(session, person, settings=settings)
    total = int(
        await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.person_id == person.id)
        )
        or 0
    )
    skip = max(0, total - limit) if offset is None else max(0, offset)
    result = await session.execute(
        select(Message)
        .where(Message.person_id == person.id)
        .order_by(Message.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all()), total, skip, conv


async def save_user_message(
    session: AsyncSession,
    person: Person,
    conversation_id: uuid.UUID,
    content: str,
    *,
    verify_owner: bool = True,
    commit: bool = True,
) -> Message:
    if verify_owner:
        await get_conversation_owned(session, person.id, conversation_id)
    msg = Message(
        conversation_id=conversation_id,
        person_id=person.id,
        role="user",
        content=content,
    )
    session.add(msg)
    from pai.domains.journey.service import record_user_message

    await record_user_message(session, person.id, content, source_id=msg.id)
    await session.flush()
    if commit:
        await session.commit()
    return msg


async def begin_chat_turn(
    session: AsyncSession,
    person: Person,
    conversation_id: uuid.UUID,
    content: str,
) -> tuple[Message, OrchestrationRun]:
    """One commit: user message + orchestration run. Caller already owns the conversation."""
    msg = await save_user_message(
        session, person, conversation_id, content, verify_owner=False, commit=False
    )
    run = OrchestrationRun(
        person_id=person.id,
        conversation_id=conversation_id,
        run_type="chat_message",
        status="running",
    )
    session.add(run)
    await session.commit()
    return msg, run


async def count_person_messages(session: AsyncSession, person_id: uuid.UUID) -> int:
    n = await session.scalar(
        select(func.count()).select_from(Message).where(Message.person_id == person_id)
    )
    return int(n or 0)


async def save_assistant_message(
    session: AsyncSession,
    person: Person,
    conversation_id: uuid.UUID,
    content: str,
    *,
    provider: str | None,
    model: str | None,
    update_title: bool = True,
) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        person_id=person.id,
        role="assistant",
        content=content,
        model_provider=provider,
        model_name=model,
    )
    session.add(msg)
    from pai.domains.journey.service import record_assistant_message

    record_assistant_message(session, person.id, content, source_id=msg.id)
    conv = await session.get(Conversation, conversation_id)
    if update_title and conv and conv.title in (None, "New conversation", "PAI"):
        conv.title = content[:80]
    await session.commit()
    await session.refresh(msg)
    return msg


async def start_orchestration_run(
    session: AsyncSession,
    person: Person,
    *,
    conversation_id: uuid.UUID | None,
    run_type: str,
) -> OrchestrationRun:
    run = OrchestrationRun(
        person_id=person.id,
        conversation_id=conversation_id,
        run_type=run_type,
        status="running",
    )
    session.add(run)
    await session.commit()
    return run
