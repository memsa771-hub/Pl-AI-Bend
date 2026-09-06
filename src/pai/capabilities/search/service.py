"""Generic search action. Provider-specific work lives in integrations."""

from __future__ import annotations

from typing import Any

from pai.integrations.search import web_search


async def search(
    *,
    query: str,
    api_key: str,
    search_depth: str,
    max_results: int,
    topic: str = "general",
) -> dict[str, Any]:
    return await web_search(
        query=query,
        api_key=api_key,
        search_depth=search_depth,
        max_results=max_results,
        topic=topic,
    )
