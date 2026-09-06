"""Tavily web search adapter. Callers go through capabilities.search."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def tavily_search(
    *,
    query: str,
    api_key: str,
    search_depth: str,
    max_results: int,
    topic: str = "general",
) -> dict[str, Any]:
    try:
        from tavily import AsyncTavilyClient
    except ImportError:
        return {"ok": False, "error": "tavily-python is not installed.", "results": [], "answer": ""}
    client = AsyncTavilyClient(api_key=api_key)
    try:
        response = await client.search(
            query=query,
            search_depth=search_depth,
            max_results=max_results,
            include_answer=True,
            topic=topic,
        )
    except Exception as exc:
        logger.warning("Tavily search failed: %s", exc)
        return {"ok": False, "error": f"Web search failed: {exc}", "results": [], "answer": ""}
    return {
        "ok": True,
        "error": "",
        "results": list(response.get("results") or []),
        "answer": str(response.get("answer") or ""),
    }
