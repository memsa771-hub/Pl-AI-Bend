from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from pai.config import Settings
from pai.platform.llm.provider import LLMProviderError
from pai.platform.llm.schemas import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMToolCall,
    LLMToolCallFunction,
)
from pai.platform.llm.stream_parse import delta_from_sse_line

logger = logging.getLogger(__name__)


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.deepseek_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def generate(self, request: LLMRequest) -> LLMResponse:
        timeout = request.timeout_seconds or self._settings.llm_timeout_seconds
        model = request.model or self._settings.llm_counseling_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = request.tools
            if request.tool_choice is not None:
                payload["tool_choice"] = request.tool_choice
        if request.response_format:
            payload["response_format"] = request.response_format
        headers = {
            "Authorization": f"Bearer {self._settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.post(
                "/chat/completions",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise LLMProviderError("LLM request timed out.") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError("LLM provider unreachable.") from exc
        if response.status_code >= 400:
            logger.warning("DeepSeek error status=%s body=%s", response.status_code, response.text[:300])
            raise LLMProviderError("LLM provider returned an error.")
        data = response.json()
        choice = data["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if not str(content).strip():
            content = message.get("reasoning_content") or message.get("reasoning") or ""
        if isinstance(content, list):
            content = "".join(
                (part.get("text") or "") if isinstance(part, dict) else str(part)
                for part in content
            )
        raw_calls = message.get("tool_calls") or []
        tool_calls = [_parse_tool_call(tc) for tc in raw_calls]
        return LLMResponse(
            content=content if isinstance(content, str) else (content or ""),
            provider=self.name,
            model=model,
            usage=data.get("usage") or {},
            tool_calls=[tc for tc in tool_calls if tc is not None],
            finish_reason=choice.get("finish_reason"),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        timeout = request.timeout_seconds or self._settings.llm_timeout_seconds
        model = request.model or self._settings.llm_counseling_model
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_api_dict() for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            payload["tools"] = request.tools
            if request.tool_choice is not None:
                payload["tool_choice"] = request.tool_choice
        if request.response_format:
            payload["response_format"] = request.response_format
        headers = {
            "Authorization": f"Bearer {self._settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with self._client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                headers=headers,
                timeout=timeout,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread())[:300]
                    logger.warning("DeepSeek stream error status=%s body=%s", response.status_code, body)
                    raise LLMProviderError("LLM provider returned an error.")
                async for line in response.aiter_lines():
                    delta = delta_from_sse_line(line)
                    if delta:
                        yield delta
        except LLMProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise LLMProviderError("LLM request timed out.") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError("LLM provider unreachable.") from exc

    async def generate_structured(
        self,
        request: LLMRequest,
        output_schema: type[BaseModel],
    ) -> BaseModel:
        schema_hint = json.dumps(output_schema.model_json_schema())
        structured_messages = list(request.messages)
        structured_messages.append(
            LLMMessage(
                role="user",
                content=(
                    "Respond with a single JSON object matching this schema "
                    f"(no markdown fences): {schema_hint}"
                ),
            )
        )
        raw = await self.generate(
            LLMRequest(
                messages=structured_messages,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                timeout_seconds=request.timeout_seconds,
                model=request.model,
                response_format={"type": "json_object"},
            )
        )
        text = raw.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            parsed: dict[str, Any] = json.loads(text)
            return output_schema.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMProviderError("LLM returned invalid structured output.") from exc


def _parse_tool_call(raw: dict[str, Any]) -> LLMToolCall | None:
    try:
        fn = raw.get("function") or {}
        return LLMToolCall(
            id=str(raw.get("id") or "tool_call"),
            type="function",
            function=LLMToolCallFunction(
                name=str(fn.get("name") or ""),
                arguments=fn.get("arguments")
                if isinstance(fn.get("arguments"), str)
                else json.dumps(fn.get("arguments") or {}),
            ),
        )
    except Exception:
        return None
