from __future__ import annotations

import json
import uuid
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pai.config import Settings, get_settings
from pai.domains.conversations import service as conv_svc
from pai.domains.conversations.models import OrchestrationRun
from pai.domains.conversations.service import begin_chat_turn, save_assistant_message
from pai.domains.documents.service import attach_documents_to_message
from pai.domains.memory.service import PersonMemoryService
from pai.domains.student.person.models import Person
from pai.intelligences.counselor.followup import _payload_from_state, handle_user_message
from pai.intelligences.counselor.opening import ensure_thread_opening
from pai.intelligences.counselor.orchestrator import PAIOrchestrator
from pai.intelligences.counselor.routing import counseling_task, counselor_web_search_enabled
from pai.interfaces.api.dependencies import get_db, require_onboarding_complete
from pai.interfaces.api.schemas import ApiErrorResponse, success
from pai.platform.database.db import get_session_factory
from pai.platform.jobs.queue import enqueue_intelligence
from pai.platform.latency import record

chat_router = APIRouter(prefix="/api/v1", tags=["chat"])


async def _person_conversation(session, person, settings):
    conv = await conv_svc.get_or_create_person_conversation(
        session, person, settings=settings
    )
    await ensure_thread_opening(session, person, conv.id, settings=settings)
    return conv


class ChatRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"message": "I want MS CS in Germany"}]
        }
    )

    message: str = Field(..., min_length=1, max_length=16000, examples=["I want MS CS in Germany"])
    attachmentIds: list[uuid.UUID] = Field(default_factory=list, max_length=8)


_AUTH_ERRORS = {
    401: {"model": ApiErrorResponse, "description": "Missing/invalid Bearer token"},
    403: {"model": ApiErrorResponse, "description": "Onboarding not completed"},
    422: {"model": ApiErrorResponse, "description": "Validation error"},
    502: {"model": ApiErrorResponse, "description": "LLM / orchestration failure"},
}


def _message_item(m) -> dict:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@chat_router.post(
    "/chat",
    status_code=status.HTTP_200_OK,
    summary="Send a counselor message",
    responses=_AUTH_ERRORS,
)
async def chat(
    body: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    setup_started = perf_counter()
    conv = await _person_conversation(session, person, settings)
    user_msg, run = await begin_chat_turn(session, person, conv.id, body.message)
    if body.attachmentIds:
        await attach_documents_to_message(
            session, person.id, user_msg.id, body.attachmentIds
        )
    record("conversation_setup", setup_started)
    gateway = getattr(request.app.state, "llm_gateway", None)
    data = await handle_user_message(
        session,
        settings,
        person,
        conv.id,
        user_msg,
        gateway=gateway,
        run=run,
        defer_intelligence=True,
    )
    return JSONResponse(content=success(data))


@chat_router.post(
    "/chat/stream",
    summary="Stream a counselor reply (SSE)",
    responses=_AUTH_ERRORS,
)
async def chat_stream(
    body: ChatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
) -> StreamingResponse:
    setup_started = perf_counter()
    conv = await _person_conversation(session, person, settings)
    user_msg, run = await begin_chat_turn(session, person, conv.id, body.message)
    if body.attachmentIds:
        await attach_documents_to_message(
            session, person.id, user_msg.id, body.attachmentIds
        )
    record("conversation_setup", setup_started)
    gateway = getattr(request.app.state, "llm_gateway", None)
    person_id = person.id
    conversation_id = conv.id
    user_message_id = str(user_msg.id)
    user_text = user_msg.content
    run_id = run.id

    async def events_body():
        factory = get_session_factory(settings)
        async with factory() as stream_session:
            row = await stream_session.execute(
                select(Person)
                .options(selectinload(Person.vault))
                .where(Person.id == person_id, Person.deleted_at.is_(None))
            )
            stream_person = row.scalar_one()
            orch = PAIOrchestrator(settings, gateway=gateway)
            orch._session = stream_session
            orch._person = stream_person
            orch._run = await stream_session.get(OrchestrationRun, run_id)
            orch._memory = PersonMemoryService(
                settings, stream_person.id, session_factory=factory
            )
            state = {
                "person_id": str(person_id),
                "conversation_id": str(conversation_id),
                "user_message_id": user_message_id,
                "user_message": user_text,
                "student_context": None,
                "student_context_json": "{}",
                "extraction_required": False,
                "fact_candidates": [],
                "observed_candidates": [],
                "candidate_results": [],
                "applied_vault_changes": [],
                "pending_confirmations": [],
                "task_proposals": [],
                "task_results": [],
                "assistant_result": None,
                "assistant_reply": "",
                "run_id": str(run_id),
                "run_status": "running",
                "errors": [],
                "orchestration_llm_calls": 0,
                "semantic_memory_context": "",
                "tool_trace": [],
            }
            state = await orch.node_load_student_context(state)
            if getattr(state.get("turn_understanding"), "research_state", "unknown") == "required":
                yield _sse("status", {"phase": "research", "message": "Checking current information."})
            first_token = True
            async for delta in orch.iter_reply_tokens(state):
                if delta and first_token:
                    record("first_sse_token", getattr(request.state, "request_started", setup_started))
                    first_token = False
                yield _sse("token", delta)
            reply = state.get("assistant_reply") or ""
            queued = enqueue_intelligence(
                stream_session,
                person_id=person_id,
                conversation_id=conversation_id,
                user_message=user_text,
                user_message_id=user_message_id,
                extraction_required=bool(state.get("extraction_required")),
                task_proposals=list(state.get("task_proposals") or []),
                run_id=str(run_id),
            )
            assistant = await save_assistant_message(
                stream_session,
                stream_person,
                conversation_id,
                reply,
                provider=settings.llm_default_provider,
                model=(settings.llm_simple_counseling_model
                       if counseling_task(user_text) == "simple_conversation"
                       else settings.llm_counseling_model),
            )
            if queued is None and orch._run is not None:
                orch._run.status = "completed"
                await stream_session.commit()
            yield _sse(
                "reply",
                {
                    "conversationId": str(conversation_id),
                    "messageId": str(assistant.id),
                    "reply": reply,
                },
            )
            done = _payload_from_state(
                conversation_id=conversation_id,
                assistant=assistant,
                graph_state=state,
                intelligence_pending=queued is not None,
            )
            yield _sse("done", done)

    async def record_stream_failure(status):
        from datetime import UTC, datetime
        async with get_session_factory(settings)() as failure_session:
            failed_run = await failure_session.get(OrchestrationRun, run_id)
            if failed_run is not None and failed_run.status != "completed":
                failed_run.status = status
                failed_run.error_code = "STREAM_CANCELLED" if status == "cancelled" else "STREAM_FAILED"
                failed_run.completed_at = datetime.now(UTC)
                await failure_session.commit()

    async def events():
        import asyncio
        try:
            async for event in events_body():
                yield event
        except asyncio.CancelledError:
            await asyncio.shield(record_stream_failure("cancelled"))
            raise
        except Exception:
            await record_stream_failure("failed")
            yield _sse("error", {"code": "STREAM_FAILED", "message": "The reply was interrupted. Please retry."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@chat_router.get(
    "/chat/messages",
    summary="Load the counselor transcript",
    responses=_AUTH_ERRORS,
)
async def get_chat_messages(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
    limit: int = Query(50, ge=1, le=100),
    offset: int | None = Query(
        None,
        ge=0,
        description="Skip this many messages from the start. Omit to load the latest page.",
    ),
) -> JSONResponse:
    await _person_conversation(session, person, settings)
    rows, total, skip, conv = await conv_svc.list_person_messages(
        session, person, settings=settings, limit=limit, offset=offset
    )
    return JSONResponse(
        content=success(
            {
                "conversationId": str(conv.id),
                "items": [_message_item(m) for m in rows],
                "total": total,
                "limit": limit,
                "offset": skip,
                "hasOlder": skip > 0,
                "hasNewer": skip + len(rows) < total,
            }
        )
    )
