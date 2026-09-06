from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LLMToolCallFunction(BaseModel):
    name: str
    arguments: str = "{}"


class LLMToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: LLMToolCallFunction


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[LLMToolCall] | None = None
    provider_output: list[dict[str, Any]] | None = None

    def to_api_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.name:
            payload["name"] = self.name
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        return payload


class LLMRequest(BaseModel):
    messages: list[LLMMessage]
    temperature: float = 0.3
    max_tokens: int = 2048
    timeout_seconds: float | None = None
    model: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    verbosity: str | None = None
    prompt_cache_key: str | None = None


class LLMResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    provider_output: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)
