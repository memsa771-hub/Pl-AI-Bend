"""OpenAI Responses transport; only final output_text reaches callers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from pai.config import Settings
from pai.platform.latency import record
from pai.platform.llm.provider import LLMProviderError
from pai.platform.llm.schemas import LLMMessage, LLMRequest, LLMResponse, LLMToolCall


def strict_schema(schema: dict) -> dict:
    """Normalize closed Pydantic objects; reject arbitrary maps/Any explicitly."""
    schema = deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        if node.get("type") == "object":
            if "properties" not in node or node.get("additionalProperties") not in (None, False):
                raise ValueError("Open-ended object needs JSON mode")
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        if not any(k in node for k in ("type", "$ref", "anyOf", "enum", "const")):
            raise ValueError("Unconstrained value needs JSON mode")
        for key in ("properties", "$defs"):
            for child in node.get(key, {}).values():
                visit(child)
        if "items" in node:
            visit(node["items"])
        for child in node.get("anyOf", []):
            visit(child)

    visit(schema)
    return schema


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(self, settings: Settings, *, transport=None):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=10),
            transport=transport,
        )

    async def aclose(self):
        await self._client.aclose()

    def _payload(self, request: LLMRequest) -> dict[str, Any]:
        items = []
        for message in request.messages:
            # Replay all response items, including encrypted reasoning, on tool continuations.
            if message.provider_output:
                items.extend(message.provider_output)
            elif message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content or "",
                    }
                )
            else:
                if message.content:
                    items.append({"role": message.role, "content": message.content})
                for call in message.tool_calls or []:
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        }
                    )
        payload: dict[str, Any] = {
            "model": request.model or self._settings.llm_counseling_model,
            "input": items,
            "max_output_tokens": request.max_tokens
            + (1024 if request.reasoning_effort not in (None, "none") else 0),
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if request.reasoning_effort:
            payload["reasoning"] = {"effort": request.reasoning_effort}
        text = {}
        if request.verbosity:
            text["verbosity"] = request.verbosity
        if request.response_format:
            text["format"] = request.response_format
        if text:
            payload["text"] = text
        if request.prompt_cache_key:
            payload["prompt_cache_key"] = request.prompt_cache_key
        if request.tools:
            payload["tools"] = [
                {"type": "function", **tool["function"], "strict": False} for tool in request.tools
            ]
            choice = request.tool_choice
            if isinstance(choice, dict) and "function" in choice:
                choice = {"type": "function", "name": choice["function"]["name"]}
            payload["tool_choice"] = choice or "auto"
        return payload

    def _response(self, data: dict, request: LLMRequest) -> LLMResponse:
        if data.get("status") != "completed":
            raise LLMProviderError("LLM response did not complete.")
        output = data.get("output") or []
        text, calls = [], []
        for item in output:
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "refusal":
                        raise LLMProviderError("LLM declined this request.")
                    if part.get("type") == "output_text":
                        text.append(part.get("text", ""))
            elif item.get("type") == "function_call":
                calls.append(
                    LLMToolCall(
                        id=item["call_id"],
                        function={
                            "name": item["name"],
                            "arguments": item["arguments"],
                        },
                    )
                )
        return LLMResponse(
            content="".join(text),
            provider=self.name,
            model=data.get("model") or request.model or "",
            usage=data.get("usage") or {},
            tool_calls=calls,
            finish_reason=data["status"],
            provider_output=output,
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            response = await self._client.post(
                "responses",
                json=self._payload(request),
                timeout=request.timeout_seconds or self._settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            return self._response(response.json(), request)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise LLMProviderError("OpenAI response request failed.") from exc

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        payload = {**self._payload(request), "stream": True}
        started = perf_counter()
        first_byte = True
        completed = False
        try:
            async with self._client.stream(
                "POST",
                "responses",
                json=payload,
                timeout=request.timeout_seconds or self._settings.llm_timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if first_byte and line:
                        record("llm_first_byte", started, provider=self.name)
                        first_byte = False
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        yield event.get("delta", "")
                    elif kind == "response.completed":
                        self._response(event["response"], request)
                        completed = True
                    elif kind in {
                        "error",
                        "response.failed",
                        "response.incomplete",
                        "response.refusal.delta",
                    }:
                        raise LLMProviderError("OpenAI stream did not complete.")
            if not completed:
                raise LLMProviderError("OpenAI stream ended before completion.")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise LLMProviderError("OpenAI streaming request failed.") from exc

    async def generate_structured(
        self, request: LLMRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        schema = output_schema.model_json_schema()
        try:
            fmt = {
                "type": "json_schema",
                "name": output_schema.__name__,
                "schema": strict_schema(schema),
                "strict": True,
            }
            messages = request.messages
        except ValueError:
            # Existing Vault contracts have arbitrary JSON maps. Do not silently
            # drop their fields to force them into the strict schema subset.
            fmt = {"type": "json_object"}
            messages = [
                *request.messages,
                LLMMessage(
                    role="user",
                    content="Return only JSON matching this schema: " + json.dumps(schema),
                ),
            ]
        response = await self.generate(
            request.model_copy(
                update={
                    "messages": messages,
                    "response_format": fmt,
                }
            )
        )
        try:
            return output_schema.model_validate_json(response.content)
        except ValidationError as exc:
            raise LLMProviderError("LLM returned invalid structured output.") from exc
