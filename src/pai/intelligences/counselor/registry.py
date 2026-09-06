from __future__ import annotations

import json
import logging
from typing import Any

from pai.intelligences.counselor.tooling import ToolContext, ToolResult, ToolSpec
from pai.intelligences.counselor.memory_tools import RememberInsightTool, RecallSemanticMemoryTool
from pai.intelligences.counselor.web_search import WebSearchTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [t.openai_tool_schema() for t in self._tools.values()]

    async def execute(
        self, name: str, arguments: dict[str, Any] | str, ctx: ToolContext
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(name=name, ok=False, content=f"Unknown tool: {name}")
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                args = {"raw": arguments}
        else:
            args = arguments or {}
        try:
            return await tool.ainvoke(args, ctx)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return ToolResult(name=name, ok=False, content=f"Tool error: {exc}")


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            WebSearchTool(),
            RecallSemanticMemoryTool(),
            RememberInsightTool(),
        ]
    )


def build_turn_registry(
    *,
    enable_web_search: bool = False,
    enable_semantic_recall: bool = False,
    enable_remember: bool = False,
) -> ToolRegistry:
    """Deterministic per-turn tool set — avoid handing every tool to every request."""
    tools: list[ToolSpec] = []
    if enable_web_search:
        tools.append(WebSearchTool())
    if enable_semantic_recall:
        tools.append(RecallSemanticMemoryTool())
    if enable_remember:
        tools.append(RememberInsightTool())
    return ToolRegistry(tools)
