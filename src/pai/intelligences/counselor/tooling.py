from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pai.config import Settings
from pai.domains.memory.service import PersonMemoryService


@dataclass
class ToolContext:
    settings: Settings
    memory: PersonMemoryService
    person_id: str
    conversation_id: str


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)


class ToolSpec(Protocol):
    name: str
    description: str
    parameters_schema: dict[str, Any]

    async def ainvoke(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...

    def openai_tool_schema(self) -> dict[str, Any]: ...
