from __future__ import annotations

from collections.abc import AsyncIterator
from time import perf_counter

from pydantic import BaseModel

from pai.config import Settings, get_settings
from pai.platform.latency import record, span
from pai.platform.llm.provider import LLMProvider
from pai.platform.llm.providers.deepseek import DeepSeekProvider
from pai.platform.llm.providers.openai_responses import OpenAIResponsesProvider
from pai.platform.llm.schemas import LLMMessage, LLMRequest, LLMResponse


class LLMGateway:
    def __init__(self, settings: Settings | None = None, *, subject: str | None = None) -> None:
        self._subject = subject
        self._settings = settings or get_settings()
        self._providers: dict[str, LLMProvider] = {}
        if self._settings.deepseek_api_key:
            self._providers["deepseek"] = DeepSeekProvider(self._settings)

        if self._settings.openai_api_key:
            self._providers["openai"] = OpenAIResponsesProvider(self._settings)

    def register_provider(self, name: str, provider: LLMProvider) -> None:
        self._providers[name] = provider

    def _provider_for_task(self, task: str) -> LLMProvider:
        name = self._settings.llm_default_provider
        provider = self._providers.get(name)
        if provider is None:
            from pai.kernel.errors import AuthError

            raise AuthError(
                code="LLM_NOT_CONFIGURED",
                message="No LLM provider is configured.",
                status_code=503,
            )
        return provider

    def _model_for_task(self, task: str) -> str:
        if task == "simple_conversation":
            return self._settings.llm_simple_counseling_model
        if task == "goal_intelligence":
            return self._settings.llm_goal_model
        if task == "complex_analysis":
            return self._settings.llm_complex_model
        if task in ("extraction", "extract_facts", "fact_extraction", "turn_understanding"):
            return self._settings.llm_extraction_model
        if task in ("document", "document_extract", "document_classification"):
            return self._settings.llm_document_model
        if task in ("document_vision", "ocr"):
            return self._settings.llm_document_vision_model
        if task in ("counseling", "student_conversation", "simple_conversation"):
            return self._settings.llm_counseling_model
        return self._settings.llm_counseling_model

    def _request_options(self, task: str) -> dict:
        counseling = task in ("counseling", "student_conversation", "simple_conversation")
        reasoning = self._settings.llm_extraction_reasoning
        if counseling:
            reasoning = self._settings.llm_counseling_reasoning
        if task == "simple_conversation":
            reasoning = "none"
        elif task in ("goal_intelligence", "complex_analysis"):
            reasoning = self._settings.llm_goal_reasoning
        if task == "turn_understanding":
            reasoning = "none"
        return {
            "reasoning_effort": reasoning,
            "verbosity": "low" if counseling else None,
            "prompt_cache_key": f"pai:{task}:v1",
            "timeout_seconds": (self._settings.llm_counseling_timeout_seconds
                                if counseling else (self._settings.turn_understanding_budget_seconds
                                if task == "turn_understanding" else self._settings.llm_timeout_seconds)),
        }

    def _max_tokens_for(self, task: str) -> int:
        if task in ("counseling", "student_conversation", "simple_conversation"):
            return int(self._settings.llm_counseling_max_tokens)
        if task in ("document", "document_extract", "document_classification"):
            return int(self._settings.document_vision_max_tokens)
        return 2048

    async def run(
        self,
        *,
        task: str,
        messages: list[LLMMessage],
        output_schema: type[BaseModel] | None = None,
        temperature: float = 0.3,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse | BaseModel:
        provider = self._provider_for_task(task)
        request = LLMRequest(
            **self._request_options(task),
            messages=messages,
            temperature=temperature,
            model=self._model_for_task(task),
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens or self._max_tokens_for(task),
        )
        from pai.platform.limits import reserve_llm
        await reserve_llm(self._settings, request, subject=self._subject)
        with span("llm_total", task=task, model=request.model):
            if output_schema is not None:
                return await provider.generate_structured(request, output_schema)
            return await provider.generate(request)

    async def stream(
        self,
        *,
        task: str,
        messages: list[LLMMessage],
        temperature: float = 0.3,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        provider = self._provider_for_task(task)
        request = LLMRequest(
            **self._request_options(task),
            messages=messages,
            temperature=temperature,
            model=self._model_for_task(task),
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens or self._max_tokens_for(task),
        )
        from pai.platform.limits import reserve_llm
        await reserve_llm(self._settings, request, subject=self._subject)
        stream_fn = getattr(provider, "stream", None)
        if stream_fn is None:
            out = await provider.generate(request)
            if out.content:
                yield out.content
            return
        started = perf_counter()
        first = True
        try:
            async for delta in stream_fn(request):
                if delta and first:
                    record("llm_first_token", started, task=task, model=request.model)
                    first = False
                yield delta
        finally:
            record("llm_total", started, task=task, model=request.model)

    async def aclose(self) -> None:
        for provider in self._providers.values():
            if hasattr(provider, "aclose"):
                await provider.aclose()
