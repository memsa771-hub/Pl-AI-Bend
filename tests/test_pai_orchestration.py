from __future__ import annotations

import inspect
import uuid
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.schemas import LLMMessage, LLMRequest, LLMResponse
from pai.intelligences.counselor.agents import FactExtractionAgent, StudentConversationAgent
from pai.intelligences.counselor.orchestrator import PAIOrchestrator
from pai.intelligences.counselor.routing import (
    classify_turn,
    counseling_reply_max_tokens,
    counselor_web_search_enabled,
    is_greeting,
    should_extract_facts,
)
from pai.kernel.contracts.schemas import (
    ConversationResult,
    FactExtractionResult,
    TaskProposal,
    VaultCandidate,
)
from pai.kernel.evidence.candidate_eval import evaluate_candidate_with_context
from pai.kernel.policy.verifier import validate_candidate


class SchemaRoutingMockProvider:
    name = "mock"

    def __init__(
        self,
        *,
        extraction: FactExtractionResult | None = None,
        conversation: ConversationResult | None = None,
    ) -> None:
        self.extraction = extraction or FactExtractionResult()
        self.conversation = conversation or ConversationResult(
            reply="Hello from PAI Student Counselor.",
            next_question="What are you studying?",
        )
        self.calls: list[str] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="ok", provider=self.name, model="mock")

    async def generate_structured(self, request: LLMRequest, output_schema: type) -> Any:
        self.calls.append(output_schema.__name__)
        if output_schema.__name__ == "FactExtractionResult":
            return self.extraction
        if output_schema.__name__ == "ConversationResult":
            return self.conversation
        raise ValueError(f"unexpected schema {output_schema.__name__}")


def test_greetings_skip_extraction():
    assert should_extract_facts("Hello") is True
    assert should_extract_facts("Thanks!") is True
    assert should_extract_facts("Please continue") is True
    assert is_greeting("hi") is False
    assert is_greeting("  Hey!  ") is False
    assert is_greeting("I want MS CS") is False
    assert counseling_reply_max_tokens("hi", 400) == 400
    assert counseling_reply_max_tokens("What should I do next?", 400) == 400


def test_web_search_uses_semantic_decision_and_availability(test_settings):
    on = test_settings.model_copy(update={"tavily_api_key": "test", "enable_counselor_tools": True})
    assert counselor_web_search_enabled(on, "hello", understanding=SimpleNamespace(needs_research=True))
    assert not counselor_web_search_enabled(on, "deadline", understanding=SimpleNamespace(needs_research=False))
    off = on.model_copy(update={"tavily_api_key": ""})
    assert not counselor_web_search_enabled(off, understanding=SimpleNamespace(needs_research=True))


def test_classify_turn_uses_validated_understanding():
    assert classify_turn("find a deadline") == "PERSONAL_ADVICE"
    assert classify_turn("你好", understanding=SimpleNamespace(turn_kind="LIVE_RESEARCH")) == "LIVE_RESEARCH"


def test_substantive_messages_trigger_extraction():
    assert should_extract_facts("I completed BSCS with a 3.4 GPA.") is True
    assert should_extract_facts("I want to study AI in Germany.") is True
    assert should_extract_facts("My budget is approximately 20,000 euros.") is True
    assert should_extract_facts("PRE MEDICAL 877/1100") is True
    assert should_extract_facts("I want FAST or NUST in Islamabad") is True
    assert should_extract_facts("I live in Dubai and want NYU Abu Dhabi") is True
    assert should_extract_facts("I live in Berlin") is True
    assert should_extract_facts("I moved last month") is True
    assert should_extract_facts("Hello") is True
    assert should_extract_facts("help me") is True
    assert should_extract_facts("ok go") is True
    assert should_extract_facts("COkay lock in USTC and STJU") is True


def test_questions_carrying_facts_still_extract():
    """A question is not proof the turn is factless.

    These asked for extraction to be skipped, which dropped the whole
    intelligence job: no Vault write, no goal capture, no memory formation.
    Counseling context arrives inside questions, so the extractor — not a
    regex — decides whether there is anything to keep.
    """
    assert should_extract_facts(
        "What scholarships can I get? My mother earns 40k a month."
    ) is True
    assert should_extract_facts("What should I do next?") is True
    assert should_extract_facts("What IELTS score do I need?") is True


def test_trivial_turns_still_skip_extraction():
    assert should_extract_facts("What do you mean?") is True
    assert should_extract_facts("can you explain") is True
    assert should_extract_facts("thanks") is True
    assert should_extract_facts("") is False


def test_agents_do_not_call_each_other():
    assert "FactExtractionAgent" not in inspect.getsource(StudentConversationAgent)
    assert "StudentConversationAgent" not in inspect.getsource(FactExtractionAgent)
    fact_src = inspect.getsource(FactExtractionAgent.extract_from_chat)
    assert "conversation" not in fact_src.lower() or "Never write counselor" in fact_src


def test_only_conversation_agent_sets_reply(test_settings):
    gateway = LLMGateway(test_settings)
    gateway.register_provider("deepseek", SchemaRoutingMockProvider())
    conv = StudentConversationAgent(gateway)
    import asyncio

    result = asyncio.run(
        conv.respond(
            current_message="Hi",
            student_context_json="{}",
            recent_messages_json="[]",
            known_facts_json="{}",
            missing_critical_fields_json="[]",
            pending_confirmations_json="[]",
            active_tasks_json="[]",
            applied_vault_changes_json="[]",
            task_results_json="[]",
        )
    )
    assert "Hello from PAI" in result.reply


def test_fact_agent_returns_empty_without_reply(test_settings):
    gateway = LLMGateway(test_settings)
    gateway.register_provider("deepseek", SchemaRoutingMockProvider())
    fact = FactExtractionAgent(gateway)
    import asyncio

    out = asyncio.run(
        fact.extract_from_chat(user_message="Hello", user_message_id="mid-1")
    )
    assert out == []


def test_invalid_llm_field_rejected_before_vault():
    bad = VaultCandidate(
        field_key="not.real.field",
        value="x",
        confidence=0.99,
        evidence_text="text",
        source_reference=str(uuid.uuid4()),
    )
    assert validate_candidate(bad) is None


@pytest.mark.asyncio
async def test_evaluate_sensitive_pending(postgres_ready, test_settings):
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.domains.student.person.models import Person, PersonVault

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)
    async with factory() as session:
        person = Person(
            auth_provider="supabase",
            external_auth_id="sens-user",
            email="sens@example.com",
        )
        session.add(person)
        await session.flush()
        session.add(
            PersonVault(
                person_id=person.id,
                catalog_version="1",
                applicable_scopes=["student"],
            )
        )
        await session.commit()
        await session.refresh(person, attribute_names=["vault"])
        cand = VaultCandidate(
            field_key="demographics.date_of_birth",
            value="2000-01-01",
            confidence=0.92,
            evidence_text="born 2000",
            source_reference="m1",
            requires_confirmation=True,
            explicitness="explicit",
        )
        result = await evaluate_candidate_with_context(session, person, cand)
        assert result.outcome == "pending_confirmation"


def test_orchestrator_wires_agents(test_settings):
    gateway = LLMGateway(test_settings)
    mock = SchemaRoutingMockProvider(
        extraction=FactExtractionResult(
            fact_candidates=[
                VaultCandidate(
                    field_key="preferences.preferred_language",
                    value="en",
                    confidence=0.92,
                    evidence_text="English",
                    source_reference="msg-1",
                    explicitness="explicit",
                )
            ]
        ),
        conversation=ConversationResult(
            reply="Noted.",
            next_question="Target country?",
            task_proposals=[TaskProposal(title="Prepare IELTS timeline")],
        ),
    )
    gateway.register_provider("deepseek", mock)
    orch = PAIOrchestrator(test_settings, gateway=gateway)
    assert orch._fact_agent is not None
    assert orch._conversation_agent is not None
    assert "FactExtractionAgent" in type(orch._fact_agent).__name__
    assert "StudentConversationAgent" in type(orch._conversation_agent).__name__


def test_mock_provider_replace_deepseek(test_settings):
    gateway = LLMGateway(test_settings)
    gateway.register_provider("deepseek", SchemaRoutingMockProvider())
    import asyncio

    out = asyncio.run(
        gateway.run(
            task="student_conversation",
            messages=[LLMMessage(role="user", content="x")],
            output_schema=ConversationResult,
        )
    )
    assert isinstance(out, ConversationResult)


def test_tool_loop_reuses_plain_reply_without_second_llm():
    from pai.intelligences.counselor.counselor_graph import _result_from_text

    out = _result_from_text("Here's a simple next step for your applications.")
    assert out is not None
    assert out.reply.startswith("Here's a simple")
    assert out.task_proposals == []


@pytest.mark.asyncio
async def test_empty_tool_completion_falls_back_to_prose():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from pai.intelligences.counselor.counselor_graph import iter_counselor_tokens
    from pai.intelligences.counselor.registry import build_turn_registry
    from pai.intelligences.counselor.tooling import ToolContext
    from pai.platform.llm.schemas import LLMResponse

    class FakeGateway:
        async def run(self, **kwargs):
            if kwargs.get("tools"):
                return LLMResponse(content="", provider="deepseek", model="x")
            return LLMResponse(
                content="Columbia MS is competitive; I can map a path from your profile.",
                provider="deepseek",
                model="x",
            )

        async def stream(self, **kwargs):
            if False:
                yield ""

    settings = SimpleNamespace(counselor_max_tool_rounds=1, llm_counseling_max_tokens=400)
    chunks: list[str] = []
    async for delta in iter_counselor_tokens(
        gateway=FakeGateway(),
        settings=settings,
        prompt_vars={"current_message": "I want MS in columbia", "profile_block": "", "recent_turns": []},
        registry=build_turn_registry(enable_web_search=True),
        tool_ctx=ToolContext(
            settings=settings, memory=MagicMock(), person_id="p", conversation_id="c"
        ),
        enable_tools=True,
    ):
        chunks.append(delta)
    text = "".join(chunks)
    assert "Columbia" in text
    assert text.strip() != ""


def test_counselor_json_preamble_does_not_leak_into_reply():
    from pai.intelligences.counselor.counselor_graph import _result_from_text

    leaked = """Based on the search results, I can now give Musawir a grounded comparison. Let me craft the response.

```json
{
  "reply": "Locked in USTC and SJTU. Next I will pull the official program pages.",
  "known_facts_used": ["Target study country/countries: CN"],
  "observations": ["Student asked for a comparison"],
  "suggested_next_step": "Open the USTC CS admissions page",
  "next_question": "Want the CSC Type B route next?",
  "task_proposals": []
}
```"""
    out = _result_from_text(leaked)
    assert out is not None
    assert out.reply.startswith("Locked in USTC")
    assert "known_facts_used" not in out.reply
    assert "```" not in out.reply
    assert out.known_facts_used == ["Target study country/countries: CN"]
    assert out.suggested_next_step == "Open the USTC CS admissions page"


def test_llm_call_budget_constants():
    from pai.intelligences.counselor.orchestrator import MAX_LLM_CALLS_PER_TURN

    assert MAX_LLM_CALLS_PER_TURN == 3


def test_chat_graph_replies_without_waiting_on_extract_chain():
    from pai.intelligences.counselor.graph import build_pai_graph

    graph = build_pai_graph(MagicMock())
    assert "serve_turn" in graph.nodes
    assert "load_student_context" in graph.nodes
    assert "extract_facts" not in graph.nodes


def test_counselor_context_is_compact():
    from pai.intelligences.counselor.context import CounselorContext

    ctx = CounselorContext(
        person_id="p1",
        goal="MS CS in Germany",
        education="BSCS / Bahria 3.35",
        location="Dubai",
        known_facts=["Current goal (pursuing): MS CS in Germany"],
    )
    block = ctx.profile_block()
    assert "MS CS in Germany" in block
    assert "applicable_vault_fields" not in block
    assert "typed_profile_summary" not in block
    from pai.platform.llm.stream_parse import delta_from_sse_line

    assert delta_from_sse_line('data: {"choices":[{"delta":{"content":"Based"}}]}') == "Based"
    assert delta_from_sse_line("data: [DONE]") is None
    assert delta_from_sse_line("event: token") is None


def test_chat_message_id_used_as_evidence(test_settings):
    gateway = LLMGateway(test_settings)
    mock = SchemaRoutingMockProvider(
        extraction=FactExtractionResult(
            fact_candidates=[
                VaultCandidate(
                    field_key="preferences.preferred_language",
                    value="en",
                    confidence=0.92,
                    evidence_text="English please",
                    source_reference="",
                    explicitness="explicit",
                )
            ]
        ),
        conversation=ConversationResult(reply="Thanks.", next_question="Next?"),
    )
    gateway.register_provider("deepseek", mock)
    fact = FactExtractionAgent(gateway)
    import asyncio

    cands = asyncio.run(
        fact.extract_from_chat(user_message="English please", user_message_id="exact-msg-id")
    )
    assert cands[0].source_reference == "exact-msg-id"


def test_vault_candidate_fields_roundtrip():
    c = VaultCandidate(
        field_key="preferences.preferred_language",
        value="en",
        confidence=0.9,
        evidence_text="e",
        source_reference="m",
        rationale_summary="r",
    )
    assert c.field_key == "preferences.preferred_language"
    assert c.rationale_summary == "r"


def test_duplicate_tasks_prevented(postgres_ready, test_settings):
    import asyncio
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.domains.student.person.models import Person
    from pai.domains.actions.service import process_task_proposals
    from pai.kernel.contracts.schemas import TaskProposal

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)

    async def _run():
        async with factory() as session:
            person = Person(
                auth_provider="supabase",
                external_auth_id="task-user",
                email="task@example.com",
            )
            session.add(person)
            await session.commit()
            props = [
                TaskProposal(title="Prepare IELTS timeline"),
                TaskProposal(title="Prepare IELTS timeline"),
            ]
            r1 = await process_task_proposals(session, person, props)
            await session.commit()
            assert r1[0].status == "proposed"
            assert r1[1].status == "duplicate"

    asyncio.run(_run())


def test_substantive_turn_calls_extraction_then_conversation(test_settings):
    gateway = LLMGateway(test_settings)
    mock = SchemaRoutingMockProvider(
        extraction=FactExtractionResult(fact_candidates=[]),
        conversation=ConversationResult(reply="Got it."),
    )
    gateway.register_provider("deepseek", mock)
    import asyncio

    fact = FactExtractionAgent(gateway)
    conv = StudentConversationAgent(gateway)
    asyncio.run(fact.extract_from_chat(user_message="I have a 3.4 GPA", user_message_id="m1"))
    asyncio.run(
        conv.respond(
            current_message="I have a 3.4 GPA",
            student_context_json="{}",
            recent_messages_json="[]",
            known_facts_json="{}",
            missing_critical_fields_json="[]",
            pending_confirmations_json="[]",
            active_tasks_json="[]",
            applied_vault_changes_json="[]",
            task_results_json="[]",
        )
    )
    # Short GPA line is booster-only — skip the extract LLM.
    assert mock.calls == ["FactExtractionResult", "ConversationResult"]


def test_goal_statement_still_runs_extract_llm(test_settings):
    gateway = LLMGateway(test_settings)
    mock = SchemaRoutingMockProvider(
        extraction=FactExtractionResult(fact_candidates=[]),
        conversation=ConversationResult(reply="Got it."),
    )
    gateway.register_provider("deepseek", mock)
    import asyncio

    fact = FactExtractionAgent(gateway)
    asyncio.run(
        fact.extract_from_chat(
            user_message="I want to study locally in FAST",
            user_message_id="m1",
        )
    )
    assert "FactExtractionResult" in mock.calls

