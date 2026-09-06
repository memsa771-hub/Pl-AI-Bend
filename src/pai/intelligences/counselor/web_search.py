from __future__ import annotations

import logging
from typing import Any

from pai.intelligences.research import research_query
from pai.intelligences.counselor.tooling import ToolContext, ToolResult

logger = logging.getLogger(__name__)


class WebSearchTool:
    """Counselor tool. Live facts go through Research Intelligence, not Tavily directly."""

    name = "web_search"
    description = (
        "Search the live web for current, factual information relevant to the student "
        "(university requirements, deadlines, scholarships, visa rules, rankings, news). "
        "Do not use for personal profile facts already in the vault."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Focused search query. Include the student's current country "
                    "and study destination; do not assume Pakistan."
                ),
            },
            "topic": {
                "type": "string",
                "enum": ["general", "news"],
                "description": "Search topic bias.",
                "default": "general",
            },
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
        api_key = (ctx.settings.tavily_api_key or "").strip()
        if not api_key:
            return ToolResult(
                name=self.name,
                ok=False,
                content=(
                    "Web search is unavailable: TAVILY_API_KEY is not configured. "
                    "Answer from known student context only and say current web facts "
                    "could not be verified."
                ),
                data={"configured": False},
            )
        topic = str(args.get("topic") or "general")
        result = await research_query(
            query=query,
            api_key=api_key,
            search_depth=ctx.settings.tavily_search_depth,
            max_results=ctx.settings.tavily_max_results,
            topic=topic,
        )
        return ToolResult(
            name=self.name,
            ok=result.ok,
            content=result.as_counselor_text(),
            data={"result_count": len(result.hits), "query": query, "configured": True},
        )
