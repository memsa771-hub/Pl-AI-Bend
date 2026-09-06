import asyncio
from types import SimpleNamespace

import pytest

from pai.intelligences.counselor.counselor_graph import iter_counselor_tokens
from pai.intelligences.counselor.orchestrator import PAIOrchestrator
from pai.intelligences.counselor.registry import build_turn_registry
from pai.intelligences.counselor.routing import counselor_web_search_enabled
from pai.platform.latency import LatencyMiddleware, request_id


@pytest.mark.parametrize(
    "message,research",
    [
        ("I got 3.4 CGPA, what should I do next?", False),
        ("I am worried about my future", False),
        ("My father can only support me a little", False),
        ("What are the current tuition fees at TU Berlin?", True),
        ("Search for scholarship deadlines", True),
        ("What IELTS score do I need?", True),
    ],
)
async def test_normal_turns_never_call_tool_decision(test_settings, message, research):
    settings = test_settings.model_copy(update={"tavily_api_key": "test"})
    enabled = counselor_web_search_enabled(settings, message)
    assert enabled is research
    if research:
        return

    class Gateway:
        async def run(self, **kwargs):
            pytest.fail("normal counseling must stream directly")

        async def stream(self, **kwargs):
            yield "Let's talk about your next step."

    chunks = [
        chunk
        async for chunk in iter_counselor_tokens(
            gateway=Gateway(),
            settings=settings,
            prompt_vars={"current_message": message},
            registry=build_turn_registry(enable_web_search=enabled),
            tool_ctx=None,
            enable_tools=enabled,
        )
    ]
    assert chunks == ["Let's talk about your next step."]


async def test_context_and_recall_still_overlap(test_settings, monkeypatch):
    context_started, recall_started = asyncio.Event(), asyncio.Event()

    async def context(*args, **kwargs):
        context_started.set()
        await asyncio.wait_for(recall_started.wait(), 1)
        return SimpleNamespace(recent_messages=[], profile_block=lambda: "profile")

    async def recall(*args):
        recall_started.set()
        await asyncio.wait_for(context_started.wait(), 1)
        return ""

    async def attachment(*args):
        return ""

    monkeypatch.setattr("pai.intelligences.counselor.orchestrator.build_counselor_context", context)
    monkeypatch.setattr("pai.domains.documents.service.attachment_note_for_message", attachment)
    orch = object.__new__(PAIOrchestrator)
    orch._session = object()
    orch._person = object()
    orch._settings = test_settings
    orch._run = None
    orch._memory = SimpleNamespace(recall=recall, hydrate_conversation=lambda _: None)
    identifier = "00000000-0000-0000-0000-000000000001"
    state = await orch.node_load_student_context(
        {
            "user_message": "I got 3.4 CGPA",
            "conversation_id": identifier,
            "user_message_id": identifier,
        }
    )
    assert state["student_context_json"] == "profile"


async def test_latency_context_lasts_through_stream(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="pai.platform.latency")
    ids, sent = [], []

    async def app(scope, receive, send):
        ids.append(request_id.get())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await asyncio.sleep(0)
        ids.append(request_id.get())
        await send({"type": "http.response.body", "body": b"done"})

    async def send(msg):
        sent.append(msg)

    await LatencyMiddleware(app)({"type": "http"}, None, send)
    assert ids[0] == ids[1] != "background"
    assert request_id.get() == "background"
    assert "stage=total_request" in caplog.text
    assert sent[0]["headers"][0][0] == b"x-request-id"


@pytest.mark.parametrize("embedded", [False, True])
async def test_worker_isolation(test_settings, fake_provider, monkeypatch, embedded):
    from unittest.mock import AsyncMock

    from pai.app import create_app, lifespan

    loops = [AsyncMock() for _ in range(3)]
    for name, worker in zip(("document", "intelligence", "goal"), loops, strict=True):
        monkeypatch.setattr(f"pai.app.{name}_worker_loop", worker)
    settings = test_settings.model_copy(
        update={
            "run_workers_in_api": embedded,
            "enable_document_worker": True,
            "enable_intelligence_worker": True,
            "enable_goal_worker": True,
        }
    )
    app = create_app(settings)
    app.state.auth_provider = fake_provider
    app.state._provider_initialized = True
    async with lifespan(app):
        await asyncio.sleep(0)
    assert all(worker.await_count == int(embedded) for worker in loops)


@pytest.mark.parametrize(
    "message,task",
    [
        ("Hi!", "simple_conversation"),
        ("Thanks", "simple_conversation"),
        ("Yes", "student_conversation"),
        ("Hi, I need a career plan", "student_conversation"),
    ],
)
def test_simple_routing_keeps_contextual_turns_on_terra(message, task):
    from pai.intelligences.counselor.routing import counseling_task

    assert counseling_task(message) == task
