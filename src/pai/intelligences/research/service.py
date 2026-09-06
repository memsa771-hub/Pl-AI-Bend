"""Research intelligence — verify current external facts. Does not write domains."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pai.capabilities.search import search
from pai.platform.latency import timed


@dataclass
class ResearchHit:
    title: str
    url: str
    snippet: str


@dataclass
class ResearchResult:
    ok: bool
    query: str
    summary: str = ""
    hits: list[ResearchHit] = field(default_factory=list)
    error: str = ""
    configured: bool = True

    def as_counselor_text(self) -> str:
        if not self.ok:
            return self.error or "Web search failed."
        lines: list[str] = []
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        for i, hit in enumerate(self.hits, 1):
            lines.append(f"{i}. {hit.title}\n   {hit.url}\n   {hit.snippet}")
        return "\n".join(lines) if lines else "No results found."


@timed("web_search")
async def research_query(
    *,
    query: str,
    api_key: str,
    search_depth: str,
    max_results: int,
    topic: str = "general",
) -> ResearchResult:
    raw: dict[str, Any] = await search(
        query=query,
        api_key=api_key,
        search_depth=search_depth,
        max_results=max_results,
        topic=topic,
    )
    if not raw.get("ok"):
        return ResearchResult(
            ok=False,
            query=query,
            error=str(raw.get("error") or "Web search failed."),
        )
    hits = [
        ResearchHit(
            title=str(item.get("title") or "Untitled"),
            url=str(item.get("url") or ""),
            snippet=(str(item.get("content") or ""))[:400],
        )
        for item in (raw.get("results") or [])[:max_results]
    ]
    return ResearchResult(
        ok=True,
        query=query,
        summary=str(raw.get("answer") or ""),
        hits=hits,
    )
