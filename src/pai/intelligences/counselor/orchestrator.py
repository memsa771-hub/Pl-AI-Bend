from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings
from pai.domains.actions.service import process_task_proposals
from pai.domains.conversations.models import Conversation, Message, OrchestrationRun
from pai.domains.goals.types import GoalWriteAction
from pai.domains.memory.formation import (
    apply_memory_drafts,
    drafts_from_turn,
)
from pai.domains.memory.service import PersonMemoryService
from pai.domains.student.person.models import Person
from pai.intelligences.counselor.agents import FactExtractionAgent, StudentConversationAgent
from pai.intelligences.counselor.checkpoint import get_graph_checkpointer
from pai.intelligences.counselor.context import (
    build_counselor_context,
    build_student_context_pack,
    context_pack_to_json,
    invalidate_counselor_cache,
)
from pai.intelligences.counselor.counselor_graph import NO_REPLY_FALLBACK, public_reply
from pai.intelligences.counselor.graph import build_pai_graph
from pai.intelligences.counselor.registry import build_turn_registry
from pai.intelligences.counselor.routing import (
    counselor_web_search_enabled,
    is_greeting,
    should_extract_facts,
)
from pai.intelligences.counselor.state import PAIState
from pai.intelligences.planner import plan_next_actions
from pai.intelligences.vault.formation import partition_candidates
from pai.kernel.contracts.schemas import (
    PendingConfirmation,
    RunError,
    VaultChange,
)
from pai.kernel.gates import accept_vault_candidates, evaluate_candidates_batch
from pai.platform.database.db import get_session_factory
from pai.platform.latency import timed
from pai.platform.llm.gateway import LLMGateway

logger = logging.getLogger(__name__)

MAX_LLM_CALLS_PER_TURN = 3


def _counselor_web_note(allow_web: bool, attachment_note: str = "") -> str:
    parts: list[str] = []
    if allow_web:
        parts.append("LIVE WEB is available via the web_search tool this turn.")
    else:
        parts.append("No live research was performed this turn. Do not claim current deadlines, prices or eligibility are verified; clarify what needs checking.")
    extra = (attachment_note or "").strip()
    if extra:
        parts.append(extra)
    return "\n".join(parts)


class PAIOrchestrator:
    """Counselor coordinator. Does not own Vault/Goals/Documents writes."""

    def __init__(
        self,
        settings: Settings,
        gateway: LLMGateway | None = None,
        *,
        checkpointer=None,
        fact_agent: FactExtractionAgent | None = None,
        conversation_agent: StudentConversationAgent | None = None,
    ) -> None:
        self._settings = settings
        self._gateway = gateway or LLMGateway(settings)
        self._fact_agent = fact_agent or FactExtractionAgent(self._gateway)
        self._conversation_agent = conversation_agent or StudentConversationAgent(
            self._gateway, settings=settings
        )
        self._checkpointer = checkpointer if checkpointer is not None else get_graph_checkpointer()
        # Optional Postgres checkpointing adds remote round-trips on every node;
        # disable via ENABLE_GRAPH_CHECKPOINT=false for lower chat latency.
        if settings.enable_graph_checkpoint:
            self._graph = build_pai_graph(self).compile(checkpointer=self._checkpointer)
        else:
            self._graph = build_pai_graph(self).compile()
        self._session: AsyncSession | None = None
        self._person: Person | None = None
        self._run: OrchestrationRun | None = None
        self._memory: PersonMemoryService | None = None

    async def run_chat_turn(
        self,
        session: AsyncSession,
        person: Person,
        conversation_id: uuid.UUID,
        user_message: Message,
        *,
        run: OrchestrationRun,
    ) -> PAIState:
        self._session = session
        self._person = person
        self._run = run
        self._memory = PersonMemoryService(
            self._settings,
            person.id,
            session_factory=get_session_factory(self._settings),
        )
        state: PAIState = {
            "person_id": str(person.id),
            "conversation_id": str(conversation_id),
            "user_message_id": str(user_message.id),
            "user_message": user_message.content,
            "student_context": None,
            "student_context_json": "{}",
            "extraction_required": should_extract_facts(user_message.content),
            "fact_candidates": [],
            "observed_candidates": [],
            "candidate_results": [],
            "applied_vault_changes": [],
            "pending_confirmations": [],
            "task_proposals": [],
            "task_results": [],
            "assistant_result": None,
            "assistant_reply": "",
            "assistant_message_id": None,
            "run_id": str(run.id),
            "run_status": "running",
            "errors": [],
            "orchestration_llm_calls": 0,
            "semantic_memory_context": "",
            "tool_trace": [],
        }
        run.current_step = "save_user_message"
        run.provider = self._settings.llm_default_provider
        graph_config = {"configurable": {"thread_id": str(run.id)}}
        try:
            final = await self._graph.ainvoke(state, config=graph_config)
            run.status = final.get("run_status", "completed")
            run.current_step = "completed"
            run.completed_at = datetime.now(UTC)
            await self._record_discovery_question(final)
            return final
        except Exception as exc:
            logger.exception("Orchestration failed")
            run.status = "failed"
            run.error_code = "ORCHESTRATION_ERROR"
            run.completed_at = datetime.now(UTC)
            raise exc
        finally:
            self._session = None
            self._person = None
            self._run = None
            self._memory = None

    async def node_save_user_message(self, state: PAIState) -> PAIState:
        if self._run:
            self._run.current_step = "load_student_context"
        return state

    async def node_load_student_context(self, state: PAIState) -> PAIState:
        assert self._session and self._person
        async def bounded_recall() -> str:
            if is_greeting(state["user_message"]) or not self._memory:
                return ""
            try:
                async with asyncio.timeout(self._settings.memory_recall_budget_seconds):
                    return await timed("semantic_recall")(lambda: self._memory.recall(state["user_message"]))()
            except TimeoutError:
                # The counselor answers with no memory of this student. That is
                # a degraded turn, not routine — log it loudly enough to notice
                # before it becomes the normal case.
                logger.warning(
                    "Memory recall exceeded MEMORY_RECALL_BUDGET_SECONDS=%ss; "
                    "replying without recalled memory",
                    self._settings.memory_recall_budget_seconds,
                )
                return ""
            except Exception:
                logger.exception("Memory recall failed; replying without recalled memory")
                return ""

        memory_task = asyncio.create_task(bounded_recall())
        try:
            # Context and recall start together; understanding never waits on recall.
            pack = await timed("context")(lambda: build_counselor_context(
                self._session, self._person,
                conversation_id=uuid.UUID(state["conversation_id"]),
                settings=self._settings, message=state["user_message"],
            ))()
            from pai.intelligences.understanding.turn import understand_turn
            understanding = await understand_turn(
                self._gateway, message=state["user_message"],
                recent=pack.recent_messages, profile=pack.profile_block(),
                candidates=pack.discovery_candidates,
                timeout_seconds=self._settings.turn_understanding_budget_seconds,
            )
            semantic = await memory_task
        finally:
            if not memory_task.done():
                memory_task.cancel()
            await asyncio.gather(memory_task, return_exceptions=True)
        state["turn_understanding"] = understanding
        state["task_proposals"] = understanding.task_proposals
        state["orchestration_llm_calls"] = (state.get("orchestration_llm_calls") or 0) + 1
        pack.conversation_focus = understanding.focus
        if understanding.question:
            pack.conversation_focus += " Suggested question (use only if still appropriate): " + understanding.question
        pack.top_discovery_candidate = understanding.question_field
        state["discovery_question"] = understanding.question
        if self._memory:
            self._memory.hydrate_conversation(pack.recent_messages)
        if semantic:
            # recall() already capped this at SEMANTIC_MEMORY_MAX_RESULTS.
            pack.relevant_memory = [
                line.strip(" -")
                for line in str(semantic).splitlines()
                if line.strip() and not line.strip().startswith("Relevant context")
            ]
        from pai.domains.documents.service import attachment_note_for_message

        note = await attachment_note_for_message(
            self._session, uuid.UUID(state["user_message_id"])
        )
        state["attachment_note"] = note
        state["semantic_memory_context"] = semantic or ""
        state["student_context"] = pack
        state["student_context_json"] = pack.profile_block()
        state["extraction_required"] = should_extract_facts(state["user_message"])
        top_field = getattr(pack, "top_discovery_candidate", None)
        state["discovery_top_field"] = top_field
        if top_field:
            state.setdefault("tool_trace", []).append(
                {
                    "service": "profile_discovery",
                    "topCandidate": top_field,
                    "reason": getattr(pack, "discovery_reason", None),
                    "missingImportant": list(getattr(pack, "missing_important_fields", None) or []),
                    "enrichmentOpportunities": list(
                        getattr(pack, "enrichment_opportunities", None) or []
                    ),
                }
            )
        if self._run:
            self._run.current_step = "serve_turn"
        return state

    async def node_route_turn(self, state: PAIState) -> PAIState:
        state["extraction_required"] = should_extract_facts(state["user_message"])
        return state

    async def node_serve_turn(self, state: PAIState) -> PAIState:
        """Counselor reply only. Extraction/Vault run after the user has the text."""
        if self._run:
            self._run.current_step = "serve_turn"
        return await self.node_run_conversation_agent(state)

    async def _extract_llm_only(self, state: PAIState) -> dict:
        pack = state.get("student_context")
        known_facts = list(getattr(pack, "known_facts", None) or [])
        from pai.domains.goals.service import list_goals
        goals = await list_goals(self._session, self._person.id)
        from sqlalchemy import select
        source = await self._session.get(Message, uuid.UUID(state["user_message_id"]))
        if source is not None:
            recent = (await self._session.execute(select(Message).where(
                Message.conversation_id == source.conversation_id,
                Message.created_at <= source.created_at
            ).order_by(Message.created_at.desc()).limit(12))).scalars().all()
            known_facts.append("Prior conversation is context, not new evidence: " + json.dumps(
                [{"role": row.role, "content": row.content} for row in reversed(recent)], ensure_ascii=False))
        known_facts.append("Existing owned goals (reference IDs only when the same pursuit): " +
            json.dumps([{"id": str(g.id), "title": g.title, "anchors": g.anchors} for g in goals], ensure_ascii=False))
        candidates = await self._fact_agent.extract_from_chat(
            user_message=state["user_message"],
            user_message_id=state["user_message_id"],
            known_facts=known_facts,
            person_id=state.get("person_id"),
            memory=self._memory,
        )
        vault, observed = partition_candidates(candidates)
        bundle = getattr(self._fact_agent, "last_bundle", None)
        return {
            "fact_candidates": vault,
            "observed_candidates": observed,
            "bundle": bundle,
            "llm_goal": getattr(bundle, "current_goal", None) if bundle is not None else None,
        }

    def _apply_extract_patch(self, state: PAIState, patch: dict) -> None:
        state["fact_candidates"] = patch.get("fact_candidates") or []
        state["observed_candidates"] = patch.get("observed_candidates") or []
        bundle = patch.get("bundle")
        if bundle is None:
            return
        state.setdefault("tool_trace", []).append(
            {
                "service": "vault_intelligence",
                "source": bundle.source.value if hasattr(bundle.source, "value") else str(bundle.source),
                "domains": bundle.domains_fired,
                "boosterHits": bundle.booster_hits,
                "candidateCount": len(bundle.candidates),
                "providerCalls": bundle.provider_calls,
                "coverage": bundle.coverage_notes,
            }
        )
        if int(getattr(bundle, "provider_calls", 0) or 0) > 0:
            state["orchestration_llm_calls"] = (state.get("orchestration_llm_calls") or 0) + 1

    async def node_extract_facts(self, state: PAIState) -> PAIState:
        if (state.get("orchestration_llm_calls") or 0) >= MAX_LLM_CALLS_PER_TURN:
            state.setdefault("errors", []).append(
                RunError(code="LLM_LIMIT", message="LLM call limit reached", step="extract_facts")
            )
            updated = await self._capture_goal(state["user_message"], llm_goal=None)
            if updated:
                state["goal_updated"] = True
            return state
        patch = await self._extract_llm_only(state)
        self._apply_extract_patch(state, patch)
        updated = await self._capture_goal(
            state["user_message"], llm_goal=patch.get("llm_goal")
        )
        if updated:
            state["goal_updated"] = True
        if self._run:
            self._run.current_step = "validate_candidates"
        return state

    async def node_validate_candidates(self, state: PAIState) -> PAIState:
        assert self._session and self._person
        state["candidate_results"] = await evaluate_candidates_batch(
            self._session, self._person, list(state.get("fact_candidates") or [])
        )
        if self._run:
            self._run.current_step = "apply_vault_changes"
        return state

    async def node_apply_vault_changes(self, state: PAIState) -> PAIState:
        assert self._session and self._person
        to_apply = []
        pending: list[PendingConfirmation] = []
        for r in state.get("candidate_results") or []:
            if r.outcome in ("accept", "reinforce"):
                to_apply.append(r.candidate)
            elif r.outcome in ("pending_confirmation", "conflict"):
                to_apply.append(r.candidate.model_copy(update={"requires_confirmation": True}))
            if r.outcome == "pending_confirmation":
                pending.append(
                    PendingConfirmation(
                        field_key=r.candidate.field_key,
                        value=r.candidate.value,
                        evidence_text=r.candidate.evidence_text,
                        source_reference=r.candidate.source_reference,
                    )
                )
            if r.outcome == "conflict":
                pending.append(
                    PendingConfirmation(
                        field_key=r.candidate.field_key,
                        value=r.candidate.value,
                        evidence_text=r.candidate.evidence_text,
                        source_reference=r.candidate.source_reference,
                    )
                )
        applied: list[VaultChange] = []
        if to_apply:
            outcomes, pend_llm = await accept_vault_candidates(
                self._session, self._person, to_apply
            )
            for o in outcomes:
                applied.append(
                    VaultChange(
                        field_key=o.field_key, status=o.status, confidence=o.confidence
                    )
                )
            for p in pend_llm:
                pending.append(
                    PendingConfirmation(
                        field_key=p.field_key,
                        value=p.value,
                        evidence_text=p.evidence_text,
                        source_reference=p.source_reference,
                    )
                )
        results = state.get("candidate_results") or []
        drafts = drafts_from_turn(
            accepted=[
                r.candidate
                for r in results
                if r.outcome in ("accept", "reinforce")
                and any(change.field_key == r.candidate.field_key and change.status in ("accepted", "reinforced", "updated") for change in applied)
            ],
            pending=[
                r.candidate for r in results if r.outcome == "pending_confirmation"
            ],
            conflicts=[r.candidate for r in results if r.outcome == "conflict"],
            observed=list(state.get("observed_candidates") or []),
        )
        if drafts:
            try:
                await apply_memory_drafts(self._session, self._person.id, drafts)
            except Exception:
                logger.exception("Memory formation failed")
                raise
        if to_apply or drafts:
            invalidate_counselor_cache(self._person.id)
        state["applied_vault_changes"] = applied
        state["pending_confirmations"] = pending
        if self._run:
            self._run.current_step = "refresh_student_context"
        return state

    async def node_refresh_student_context(self, state: PAIState) -> PAIState:
        assert self._session and self._person
        applied = state.get("applied_vault_changes") or []
        if not applied and not state.get("goal_updated"):
            # No vault mutations — keep context from load_student_context.
            return state
        applied_json = [c.model_dump() for c in applied]
        pack = await build_student_context_pack(
            self._session,
            self._person,
            conversation_id=uuid.UUID(state["conversation_id"]),
            settings=self._settings,
            applied_vault_changes_turn=applied_json,
        )
        state["student_context"] = pack
        state["student_context_json"] = context_pack_to_json(pack)
        return state

    async def node_run_conversation_agent(self, state: PAIState) -> PAIState:
        if (state.get("orchestration_llm_calls") or 0) >= MAX_LLM_CALLS_PER_TURN:
            state["assistant_reply"] = (
                "I'm having trouble processing that right now. Please try again shortly."
            )
            state["run_status"] = "degraded"
            return state
        pack = state.get("student_context")
        semantic_ctx = state.get("semantic_memory_context") or ""
        known_facts_lines: list[str] = []
        profile_block = ""
        recent: list = []
        if pack is not None:
            known_facts_lines = list(getattr(pack, "known_facts", None) or [])
            if hasattr(pack, "profile_block"):
                profile_block = pack.profile_block()
            recent = list(getattr(pack, "recent_messages", None) or [])
        allow_web = counselor_web_search_enabled(self._settings, state["user_message"], state.get("turn_understanding"))
        registry = build_turn_registry(
            enable_web_search=allow_web,
            enable_semantic_recall=False,  # already prefetched into semantic_ctx
            enable_remember=False,  # avoid extra tool round-trips on normal turns
        )
        result = await self._conversation_agent.respond(
            current_message=state["user_message"],
            student_context_json=state.get("student_context_json") or "{}",
            known_facts_lines=known_facts_lines,
            profile_block=profile_block,
            recent_messages_json=json.dumps(recent),
            pending_confirmations_json="[]",
            applied_vault_changes_json="[]",
            task_results_json="[]",
            semantic_memory_context=semantic_ctx,
            memory=self._memory,
            person_id=state["person_id"],
            conversation_id=state["conversation_id"],
            tool_registry=registry,
            enable_tools=allow_web,
            web_search_available=allow_web,
            extra_note=str(state.get("attachment_note") or ""),
        )
        state["assistant_result"] = result
        # public_reply() returning empty is a decision, not a miss: the output was
        # JSON or the model's own reasoning. Falling back to the raw text here
        # would hand the student exactly what the filter just rejected.
        reply = public_reply(result.reply)
        if reply.startswith("{") or "```" in reply:
            reply = ""
        # Never ship an empty bubble: it reads as a broken app and titles the
        # conversation "". The streaming path already substitutes this string.
        state["assistant_reply"] = reply or NO_REPLY_FALLBACK
        if not state["assistant_reply"]:
            logger.warning("Counselor returned empty prose; student will see a blank reply")
        state["task_proposals"] = result.task_proposals
        prior_trace = list(state.get("tool_trace") or [])
        prior_trace.extend(self._conversation_agent.last_tool_trace or [])
        state["tool_trace"] = prior_trace
        state["orchestration_llm_calls"] = (state.get("orchestration_llm_calls") or 0) + 1
        if self._memory:
            # Record what the student was actually shown. Passing result.reply
            # here would put suppressed reasoning back into conversation memory,
            # where it is replayed as an example of our own voice.
            self._memory.record_turn(
                user=state["user_message"], assistant=state["assistant_reply"]
            )
        if self._run:
            self._run.current_step = "process_tasks"
        return state

    async def finish_intelligence(self, state: PAIState) -> PAIState:
        """Vault/memory/tasks after the student already has the reply."""
        if state.get("extraction_required"):
            try:
                patch = await self._extract_llm_only(state)
                self._apply_extract_patch(state, patch)
                updated = await self._capture_goal(
                    state["user_message"], llm_goal=patch.get("llm_goal")
                )
                if updated:
                    state["goal_updated"] = True
                    invalidate_counselor_cache(self._person.id)
                state = await self.node_validate_candidates(state)
                state = await self.node_apply_vault_changes(state)
            except Exception:
                logger.exception("Post-reply extraction failed")
                raise
        return await self.node_process_tasks(state)

    async def iter_reply_tokens(self, state: PAIState):
        from pai.intelligences.counselor.counselor_graph import iter_counselor_tokens
        from pai.intelligences.counselor.tooling import ToolContext

        pack = state.get("student_context")
        profile_block = pack.profile_block() if pack is not None and hasattr(pack, "profile_block") else ""
        recent = list(getattr(pack, "recent_messages", None) or []) if pack is not None else []
        allow_web = counselor_web_search_enabled(self._settings, state["user_message"], state.get("turn_understanding"))
        registry = build_turn_registry(
            enable_web_search=allow_web,
            enable_semantic_recall=False,
            enable_remember=False,
        )
        tool_ctx = ToolContext(
            settings=self._settings,
            memory=self._memory,
            person_id=state["person_id"],
            conversation_id=state["conversation_id"],
        )
        prompt_vars = {
            "current_message": state["user_message"],
            "profile_block": profile_block,
            "recent_turns": recent,
            "web_note": _counselor_web_note(
                allow_web, str(state.get("attachment_note") or "")
            ),
        }
        chunks: list[str] = []
        async for delta in iter_counselor_tokens(
            gateway=self._gateway,
            settings=self._settings,
            prompt_vars=prompt_vars,
            registry=registry,
            tool_ctx=tool_ctx,
            enable_tools=allow_web,
        ):
            chunks.append(delta)
            yield delta
        text = "".join(chunks)
        # iter_counselor_tokens buffers and filters the opening before yielding,
        # so whatever arrived here is what the student saw. Store exactly that:
        # blanking it would make the saved row disagree with the screen (the
        # message vanishes on reload) and would strip the "?" that
        # _record_discovery_question needs to log the gap it just asked about.
        state["assistant_reply"] = text.strip() or NO_REPLY_FALLBACK
        state["assistant_result"] = None
        if self._memory:
            self._memory.record_turn(user=state["user_message"], assistant=state["assistant_reply"])
        await self._record_discovery_question(state)

    async def node_process_tasks(self, state: PAIState) -> PAIState:
        assert self._session and self._person
        proposals = plan_next_actions(state)
        if proposals:
            results = await process_task_proposals(
                self._session,
                self._person,
                proposals,
                conversation_id=uuid.UUID(state["conversation_id"]),
            )
            await self._session.flush()
            state["task_results"] = results
        if self._run:
            self._run.current_step = "save_assistant_message"
        return state

    async def node_save_assistant_message(self, state: PAIState) -> PAIState:
        state["run_status"] = state.get("run_status") or "completed"
        if self._run:
            self._run.decision_rationale = (
                state.get("assistant_result").suggested_next_step
                if state.get("assistant_result")
                else None
            )
        return state

    async def _capture_goal(self, text: str, *, llm_goal) -> bool:
        assert self._session and self._person
        if self._run is None:
            return False
        conversation_id = getattr(self._run, "conversation_id", None)
        if conversation_id is None:
            return False
        try:
            from pai.domains.journey.service import record_goal_event
            from pai.intelligences.goals.resolver import resolve as resolve_goal

            resolver_result = await resolve_goal(
                self._session,
                self._person.id,
                conversation_id,
                llm_goal=llm_goal,
                user_message=text,
            )
            if resolver_result.action == GoalWriteAction.NONE.value or resolver_result.goal is None:
                return False
            kind = {
                GoalWriteAction.CREATE.value: "goal.created",
                GoalWriteAction.CREATE_SECONDARY.value: "goal.created",
                GoalWriteAction.SWITCH.value: "goal.changed",
                GoalWriteAction.REINFORCE.value: "goal.changed",
            }.get(resolver_result.action)
            if kind and resolver_result.action != GoalWriteAction.REINFORCE.value:
                await record_goal_event(
                    self._session,
                    self._person.id,
                    kind=kind,
                    title=resolver_result.goal.title,
                    goal_id=resolver_result.goal.id,
                )
            logger.debug(
                "Goal resolver: action=%s goal_id=%s enqueued=%s",
                resolver_result.action,
                resolver_result.goal.id,
                resolver_result.intelligence_enqueued,
            )
            return True
        except Exception:
            logger.exception("Goal resolver failed")
            raise

    async def _record_discovery_question(self, state: PAIState) -> None:
        """Persist which gap was surfaced this turn (doc §7 Rule 7 — don't
        keep re-suggesting the same field turn after turn). Best-effort: a
        "?" in the reply is our deterministic signal that *something* was
        asked, since the streaming path has no structured next_question.
        """
        field_key = state.get("discovery_top_field")
        if not field_key or self._session is None:
            return
        reply = state.get("assistant_reply") or ""
        question = state.get("discovery_question")
        if not question or question.casefold() not in reply.casefold():
            return
        conversation_id = state.get("conversation_id")
        if not conversation_id:
            return
        try:
            conv = await self._session.get(Conversation, uuid.UUID(conversation_id))
            if conv is not None:
                conv.last_discovery_field_key = field_key
                conv.last_discovery_asked_at = datetime.now(UTC)
        except Exception:
            logger.exception("Failed to record discovery question (non-fatal)")

    async def _inject_goal_facts(self, state: PAIState) -> None:
        assert self._session and self._person
        from pai.domains.journey.service import goal_fact_lines

        pack = state.get("student_context")
        if pack is None:
            return
        lines = await goal_fact_lines(self._session, self._person.id)
        rest = [
            item
            for item in (getattr(pack, "known_facts", None) or [])
            if not str(item).startswith("Current goal")
            and not str(item).startswith("Previous goal")
        ]
        pack.known_facts = lines + rest
        state["student_context"] = pack
        state["student_context_json"] = context_pack_to_json(pack)
