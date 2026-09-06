from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pai.config import Settings
from pai.kernel.errors import AuthError
from pai.domains.conversations.models import Message, OrchestrationRun
from pai.domains.conversations.service import save_assistant_message
from pai.platform.database.db import get_session_factory
from pai.platform.llm.gateway import LLMGateway
from pai.intelligences.counselor.context import chat_stay_payload
from pai.intelligences.counselor.orchestrator import PAIOrchestrator
from pai.platform.jobs.queue import enqueue_intelligence
from pai.domains.memory.service import PersonMemoryService
from pai.domains.student.person.models import Person

logger = logging.getLogger(__name__)


def _payload_from_state(
    *,
    conversation_id: uuid.UUID,
    assistant: Message,
    graph_state: dict,
    intelligence_pending: bool,
) -> dict:
    vault_updates = [
        c.model_dump() if hasattr(c, "model_dump") else c
        for c in (graph_state.get("applied_vault_changes") or [])
    ]
    pending = [
        p.model_dump() if hasattr(p, "model_dump") else p
        for p in (graph_state.get("pending_confirmations") or [])
    ]
    task_results = [
        t.model_dump() if hasattr(t, "model_dump") else t
        for t in (graph_state.get("task_results") or [])
        if (getattr(t, "status", None) if not isinstance(t, dict) else t.get("status"))
        not in ("rejected", "duplicate")
    ]
    result = graph_state.get("assistant_result")
    next_question = None
    suggested = None
    if result is not None:
        if isinstance(result, dict):
            next_question = result.get("next_question")
            suggested = result.get("suggested_next_step")
        else:
            next_question = getattr(result, "next_question", None)
            suggested = getattr(result, "suggested_next_step", None)
    stay = chat_stay_payload(
        graph_state.get("student_context"),
        next_question=next_question,
        suggested_next_step=suggested,
    )
    return {
        "conversationId": str(conversation_id),
        "messageId": str(assistant.id),
        "reply": graph_state.get("assistant_reply") or assistant.content,
        "vaultUpdates": vault_updates,
        "pendingConfirmations": pending,
        "taskResults": task_results,
        "toolTrace": list(graph_state.get("tool_trace") or []),
        "intelligencePending": intelligence_pending,
        **stay,
    }


async def run_intelligence_followup(
    *,
    settings: Settings,
    gateway: LLMGateway | None,
    person_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_message: str,
    user_message_id: str,
    extraction_required: bool,
    task_proposals: list,
    run_id: str | None,
) -> None:
    factory = get_session_factory(settings)
    async with factory() as session:
        person = (
            await session.execute(
                select(Person)
                .options(selectinload(Person.vault))
                .where(Person.id == person_id, Person.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if person is None:
            return
        orch = PAIOrchestrator(settings, gateway=gateway)
        orch._session = session
        orch._person = person
        orch._memory = PersonMemoryService(
            settings, person.id, session_factory=factory
        )
        if run_id:
            orch._run = await session.get(OrchestrationRun, uuid.UUID(run_id), with_for_update=True, populate_existing=True)
            if orch._run is not None and (orch._run.person_id != person_id or orch._run.conversation_id != conversation_id):
                raise ValueError("Follow-up run ownership mismatch")
            if orch._run is not None and orch._run.status == "completed":
                return
        state = {
            "person_id": str(person_id),
            "conversation_id": str(conversation_id),
            "user_message_id": user_message_id,
            "user_message": user_message,
            "extraction_required": extraction_required,
            "task_proposals": task_proposals or [],
            "fact_candidates": [],
            "observed_candidates": [],
            "tool_trace": [],
            "orchestration_llm_calls": 1,
        }
        try:
            await orch.finish_intelligence(state)
            if orch._run is not None:
                orch._run.status = "completed"
            await session.commit()
            from pai.domains.memory.formation import embed_pending_memories
            await embed_pending_memories(factory, person_id)
        except Exception:
            logger.exception("Intelligence follow-up failed person=%s", person_id)
            await session.rollback()
            raise


async def handle_user_message(
    session: AsyncSession,
    settings: Settings,
    person: Person,
    conversation_id: uuid.UUID,
    user_message: Message,
    *,
    orchestrator: PAIOrchestrator | None = None,
    gateway: LLMGateway | None = None,
    run: OrchestrationRun | None = None,
    defer_intelligence: bool = False,
) -> dict:
    if person.vault is None:
        raise AuthError(
            code="VAULT_NOT_READY",
            message="Person vault not initialized. Call bootstrap first.",
            status_code=400,
        )
    orch = orchestrator or PAIOrchestrator(settings, gateway=gateway)
    if run is None:
        from pai.domains.conversations.service import start_orchestration_run

        run = await start_orchestration_run(
            session, person, conversation_id=conversation_id, run_type="chat_message"
        )
        await session.commit()
    try:
        graph_state = await orch.run_chat_turn(
            session, person, conversation_id, user_message, run=run
        )
    except AuthError:
        raise
    except Exception as exc:
        run.status = "failed"
        run.error_code = "LLM_ERROR"
        from datetime import UTC, datetime

        run.completed_at = datetime.now(UTC)
        await session.commit()
        from pai.platform.llm.provider import LLMProviderError

        if isinstance(exc, LLMProviderError):
            raise exc
        raise AuthError(
            code="ORCHESTRATION_FAILED",
            message="Counselor request failed.",
            status_code=502,
        ) from exc
    reply = graph_state.get("assistant_reply") or ""
    extraction_required = bool(graph_state.get("extraction_required"))
    task_proposals = list(graph_state.get("task_proposals") or [])
    queued = None
    if defer_intelligence:
        queued = enqueue_intelligence(
            session,
            person_id=person.id,
            conversation_id=conversation_id,
            user_message=user_message.content,
            user_message_id=str(user_message.id),
            extraction_required=extraction_required,
            task_proposals=task_proposals,
            run_id=str(run.id),
        )
    from pai.intelligences.counselor.routing import counseling_task

    assistant = await save_assistant_message(
        session,
        person,
        conversation_id,
        reply,
        provider=settings.llm_default_provider,
        model=(settings.llm_simple_counseling_model
               if counseling_task(user_message.content) == "simple_conversation"
               else settings.llm_counseling_model),
    )
    if defer_intelligence:
        if queued is None:
            run.status = "completed"
            await session.commit()
        return _payload_from_state(
            conversation_id=conversation_id,
            assistant=assistant,
            graph_state=graph_state,
            intelligence_pending=queued is not None,
        )
    graph_state = await orch.finish_intelligence(graph_state)
    await session.commit()
    return _payload_from_state(
        conversation_id=conversation_id,
        assistant=assistant,
        graph_state=graph_state,
        intelligence_pending=False,
    )
