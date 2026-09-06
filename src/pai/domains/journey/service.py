from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.journey.models import PersonEvent

_SUMMARY_MAX = 240


def append_event(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    kind: str,
    summary: str,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> PersonEvent:
    row = PersonEvent(
        person_id=person_id,
        kind=kind,
        summary=(summary or "")[:_SUMMARY_MAX],
        source_type=source_type,
        source_id=source_id,
        payload=payload or {},
    )
    session.add(row)
    return row


async def record_user_message(
    session: AsyncSession,
    person_id: uuid.UUID,
    content: str,
    *,
    source_id: uuid.UUID | None = None,
) -> None:
    append_event(
        session,
        person_id,
        kind="chat.user",
        summary=content,
        source_type="chat",
        source_id=source_id,
    )


def record_assistant_message(
    session: AsyncSession,
    person_id: uuid.UUID,
    content: str,
    *,
    source_id: uuid.UUID | None = None,
) -> None:
    append_event(
        session,
        person_id,
        kind="chat.assistant",
        summary=content,
        source_type="chat",
        source_id=source_id,
    )


async def record_onboarding(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    intent: str | None,
) -> None:
    append_event(
        session,
        person_id,
        kind="onboarding.completed",
        summary="Onboarding completed",
        source_type="onboarding",
    )
    cleaned = (intent or "").strip()
    if cleaned:
        append_event(
            session,
            person_id,
            kind="goal.created",
            summary=cleaned[:_SUMMARY_MAX],
            source_type="onboarding",
        )


async def record_goal_event(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    kind: str,
    title: str,
    goal_id: uuid.UUID | None = None,
) -> None:
    """Journey timeline only. Canonical current goal lives in domains.goals."""
    append_event(
        session,
        person_id,
        kind=kind,
        summary=(title or "")[:_SUMMARY_MAX],
        source_type="goal",
        source_id=goal_id,
        payload={"goalId": str(goal_id)} if goal_id else {},
    )


def record_vault_applied(
    session: AsyncSession,
    person_id: uuid.UUID,
    field_keys: list[str],
) -> None:
    if not field_keys:
        return
    keys = field_keys[:12]
    append_event(
        session,
        person_id,
        kind="vault.applied",
        summary="Vault updated: " + ", ".join(keys),
        source_type="vault",
        payload={"fieldKeys": keys},
    )


def record_document_processed(
    session: AsyncSession,
    person_id: uuid.UUID,
    *,
    document_id: uuid.UUID,
    filename: str,
) -> None:
    append_event(
        session,
        person_id,
        kind="document.processed",
        summary=f"Document processed: {filename}"[:_SUMMARY_MAX],
        source_type="document",
        source_id=document_id,
    )


async def list_recent_events(
    session: AsyncSession, person_id: uuid.UUID, *, limit: int = 20
) -> list[PersonEvent]:
    result = await session.execute(
        select(PersonEvent)
        .where(PersonEvent.person_id == person_id)
        .order_by(PersonEvent.occurred_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def goal_fact_lines(session: AsyncSession, person_id: uuid.UUID) -> list[str]:
    from pai.domains.goals.service import goal_fact_lines as _goal_fact_lines

    return await _goal_fact_lines(session, person_id)


def event_to_public(row: PersonEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "summary": row.summary,
        "sourceType": row.source_type,
        "occurredAt": row.occurred_at.isoformat() if row.occurred_at else None,
    }
