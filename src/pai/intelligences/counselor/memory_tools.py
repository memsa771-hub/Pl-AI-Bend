from __future__ import annotations

import re
from typing import Any

from pai.intelligences.counselor.tooling import ToolContext, ToolResult

# Audit lines the pipeline once wrote into memory. They are still live and
# recallable in older data — they occupy a recall slot and put internal
# vocabulary into the counselor's context. Memory holds facts about the
# student, never our own telemetry.
_SYSTEM_LOG = re.compile(
    r"^\s*(?:Vault (?:accepted|rejected|applied)|VaultIntel\[|"
    r"Extraction (?:completed|failed)|Orchestration )",
    re.I,
)


def is_system_log_text(text: str) -> bool:
    return bool(_SYSTEM_LOG.match(text or ""))


class RecallSemanticMemoryTool:
    name = "recall_semantic_memory"
    description = (
        "Retrieve long-term semantic memories about this student (preferences, constraints, "
        "past counseling insights). Use when the vault pack may not cover soft context."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to recall (e.g. 'budget constraints', 'preferred countries').",
            }
        },
        "required": ["query"],
    }

    def openai_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    async def ainvoke(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(name=self.name, ok=False, content="Missing query.")
        context = await ctx.memory.recall(query)
        if not context:
            return ToolResult(
                name=self.name,
                ok=True,
                content="No relevant long-term memories found.",
                data={"hits": 0},
            )
        return ToolResult(name=self.name, ok=True, content=context, data={"hits": 1})


class RememberInsightTool:
    """Stores non-vault insights. Does NOT mutate Person Vault."""

    name = "remember_insight"
    description = (
        "Persist a durable counseling insight or preference that is NOT a structured vault field "
        "(e.g. 'prefers evening study plans', 'anxious about visas'). Never store secrets or "
        "invented facts."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "insight": {
                "type": "string",
                "description": "One clear sentence the system should remember long-term.",
            },
            "kind": {
                "type": "string",
                "enum": ["preference", "constraint", "insight", "goal"],
                "default": "insight",
            },
        },
        "required": ["insight"],
    }

    def openai_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    async def ainvoke(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        insight = str(args.get("insight") or "").strip()
        if not insight:
            return ToolResult(name=self.name, ok=False, content="Missing insight.")
        if is_system_log_text(insight):
            return ToolResult(
                name=self.name,
                ok=False,
                content="Memory stores facts about the student, not system activity.",
            )
        kind = str(args.get("kind") or "insight")
        memory_id = await ctx.memory.remember(
            insight,
            metadata={
                "type": kind,
                "source": "counselor_tool",
                "conversation_id": ctx.conversation_id,
            },
        )
        return ToolResult(
            name=self.name,
            ok=True,
            content=f"Remembered insight ({memory_id}).",
            data={"memory_id": memory_id},
        )
