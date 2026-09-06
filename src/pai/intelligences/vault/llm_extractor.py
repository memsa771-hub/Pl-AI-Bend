from __future__ import annotations

import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from pai.intelligences.vault.ground import ground_candidates
from pai.intelligences.vault.types import ExtractionRequest, SourceKind
from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.schemas import LLMMessage
from pai.kernel.contracts.schemas import FactExtractionResult, GoalExtract, VaultCandidate
from pai.domains.student.vault.catalog import extraction_catalog_hint

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"
MAX_REPAIR = 1


def _render(name: str, **kwargs: object) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_PROMPTS)),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env.get_template(name).render(**kwargs)


class OmnibusLLMExtractor:
    """Single strong multi-domain LLM pass (AgentSpan-style specialist, one call)."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway
        self.last_goal: GoalExtract | None = None

    async def extract(self, request: ExtractionRequest) -> list[VaultCandidate]:
        self.last_goal = None
        source = request.source.value if isinstance(request.source, SourceKind) else str(request.source)
        prompt = _render(
            "omnibus.v1.jinja2",
            source=source,
            source_reference=request.source_reference,
            document_type_hint=request.document_type_hint or "",
            known_facts=request.known_facts or [],
            catalog_hint=extraction_catalog_hint(),
            text=request.text,
        )
        result = await self._run(prompt, task=_task_for(request.source))
        for c in result.fact_candidates:
            c.source_type = _schema_source(request.source)  # type: ignore[assignment]
            if not c.source_reference:
                c.source_reference = request.source_reference
        grounded = ground_candidates(list(result.fact_candidates), request.text or "")
        self.last_goal = result.current_goal
        return grounded

    async def _run(self, user_prompt: str, *, task: str) -> FactExtractionResult:
        last_err: Exception | None = None
        for attempt in range(MAX_REPAIR + 1):
            try:
                out = await self._gateway.run(
                    task=task,
                    messages=[
                        LLMMessage(
                            role="system",
                            content=(
                                "You are PAI Vault Intelligence. Extract every potentially "
                                "persistent fact from the source — do not summarize or decide "
                                "importance. Preserve evidence spans and assertion_status "
                                "(explicit|inferred|uncertain|negated|hypothetical). Never invent "
                                "facts. Map to the Vault catalog when possible; otherwise use "
                                "memory.observed. For CVs extract every distinct school, job, "
                                "skill, project, certification, test score, and location named. "
                                "PAI is global: copy names the student used; do not assume "
                                "Pakistan or any default university list. Classify current_goal.kind "
                                "as life_aim, turn_action, or none (any language, including Roman "
                                "Urdu). Set current_goal.goal_type to admission, job, internship, "
                                "or general when kind is life_aim. evidence_text must be a verbatim span of the source. "
                                "Never write counselor replies. Return JSON only."
                            ),
                        ),
                        LLMMessage(role="user", content=user_prompt),
                    ],
                    output_schema=FactExtractionResult,
                    temperature=0.05,
                )
                assert isinstance(out, FactExtractionResult)
                return out
            except Exception as exc:
                last_err = exc
                logger.warning("Vault intel LLM extract attempt %s failed", attempt + 1)
        raise last_err or RuntimeError("vault intelligence extraction failed")


def _task_for(source: SourceKind) -> str:
    if source == SourceKind.DOCUMENT:
        return "document_extract"
    return "fact_extraction"


def _schema_source(source: SourceKind) -> str:
    if source == SourceKind.DOCUMENT:
        return "document"
    if source in (SourceKind.LINKEDIN, SourceKind.SOCIAL, SourceKind.THIRD_PARTY):
        return "system"
    return "chat"
