"""Optional bounded neural reranker; vector order is the no-network fallback."""
import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

async def rerank(query, entries, top_k, settings):
    fallback = entries[:top_k]
    if not settings.memory_rerank_url or not entries:
        return fallback
    try:
        async with asyncio.timeout(settings.memory_rerank_budget_seconds):
            async with httpx.AsyncClient(timeout=settings.memory_rerank_budget_seconds) as client:
                response = await client.post(settings.memory_rerank_url,
                    headers={"Authorization": f"Bearer {settings.memory_rerank_api_key}"},
                    json={"model": settings.memory_rerank_model, "query": query,
                          "documents": [entry.content for entry in entries], "top_n": top_k})
                response.raise_for_status()
                results = response.json()["results"]
        indices = [item["index"] for item in results]
        if (len(indices) != min(top_k, len(entries)) or len(set(indices)) != len(indices)
                or any(type(i) is not int or i < 0 or i >= len(entries) for i in indices)):
            return fallback
        return [entries[i] for i in indices]
    except Exception:
        logger.info("Memory reranker unavailable; retaining vector order")
        return fallback
