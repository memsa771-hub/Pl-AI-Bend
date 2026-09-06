"""Search provider dispatch. Add providers here instead of if/else in capabilities."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from pai.integrations.connectors.tavily import tavily_search

SearchFn = Callable[..., Awaitable[dict[str, Any]]]

PROVIDERS: dict[str, SearchFn] = {
    "tavily": tavily_search,
}


async def web_search(
    *,
    query: str,
    api_key: str,
    search_depth: str,
    max_results: int,
    topic: str = "general",
    provider: str = "tavily",
) -> dict[str, Any]:
    fn = PROVIDERS.get(provider)
    if fn is None:
        return {
            "ok": False,
            "error": f"Unknown search provider: {provider}",
            "results": [],
            "answer": "",
        }
    return await fn(
        query=query,
        api_key=api_key,
        search_depth=search_depth,
        max_results=max_results,
        topic=topic,
    )
