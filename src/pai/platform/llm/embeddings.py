"""Embedding provider for semantic memory recall.

Recall matched shared words before this: a question ("how can I afford this?")
and the stored fact ("finance.household_income: 40000/month") have no words in
common, so the memory scored zero and was dropped. Embeddings compare meaning
instead, so the fact is reachable however the student phrases the question.

DeepSeek publishes no embeddings endpoint (/v1/embeddings returns 404), so this
is a separate provider from the counseling LLM. It is swappable: point
EMBEDDING_PROVIDER elsewhere and re-embed.

Every failure path returns None rather than raising. Recall then falls back to
lexical ranking, so a missing key or a provider outage degrades memory quality
without taking chat down.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Protocol

from pai.config import Settings, get_settings

logger = logging.getLogger(__name__)

# One warning per process, not one per turn.
_warned = False


def _warn_once(message: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        logger.warning("%s; semantic recall falls back to lexical ranking", message)


class EmbeddingProvider(Protocol):
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]] | None: ...


class OpenAIEmbeddingProvider:
    """OpenAI embeddings (text-embedding-3-small, 1536 dims by default)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.dimensions = self._settings.embedding_dimensions
        self._client = None
        self._failed = False
        self._lock = asyncio.Lock()
        # Cheap running totals so spend is visible without a metrics backend.
        self.calls = 0
        self.tokens = 0

    async def _ensure_client(self):
        if self._client is not None or self._failed:
            return self._client
        async with self._lock:
            if self._client is not None or self._failed:
                return self._client
            key = (self._settings.openai_api_key or "").strip()
            if not key:
                self._failed = True
                _warn_once("OPENAI_API_KEY is not set")
                return None
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(
                    api_key=key,
                    base_url=self._settings.openai_base_url or None,
                    timeout=self._settings.embedding_timeout_seconds,
                )
            except Exception:
                self._failed = True
                logger.exception("Embedding client unavailable")
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        clean = [(t or "").strip() for t in texts]
        if not any(clean):
            return None
        client = await self._ensure_client()
        if client is None:
            return None
        started = time.perf_counter()
        try:
            response = await client.embeddings.create(
                model=self._settings.embedding_model,
                input=clean,
                dimensions=self.dimensions,
            )
            # The API preserves input order, but index is authoritative.
            ordered = sorted(response.data, key=lambda d: d.index)
            vectors = [list(d.embedding) for d in ordered]
            tokens = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0
            self.calls += 1
            self.tokens += tokens
            # One line per call: enough to see spend and latency in the logs
            # without adding a metrics dependency.
            logger.info(
                "embeddings: %s texts, %s tokens, %.0fms (session totals: %s calls, %s tokens)",
                len(clean),
                tokens,
                (time.perf_counter() - started) * 1000,
                self.calls,
                self.tokens,
            )
        except Exception:
            logger.exception("Embedding request failed")
            return None
        # A vector of the wrong width cannot be stored in vector(N) and would
        # fail per-row at write time. Refuse the whole batch loudly instead.
        bad = next((v for v in vectors if len(v) != self.dimensions), None)
        if bad is not None:
            logger.error(
                "Embedding dimension mismatch: model %s returned %s, EMBEDDING_DIMENSIONS "
                "is %s and the vector column is fixed at that width. Recall stays lexical "
                "until the model and the column agree.",
                self._settings.embedding_model,
                len(bad),
                self.dimensions,
            )
            return None
        return vectors


_provider: EmbeddingProvider | None = None


def get_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider | None:
    """Process-wide provider, or None when embeddings are off/unconfigured."""
    global _provider
    s = settings or get_settings()
    if not s.enable_semantic_embeddings:
        return None
    if not (s.openai_api_key or "").strip():
        _warn_once("OPENAI_API_KEY is not set")
        return None
    if _provider is None:
        _provider = OpenAIEmbeddingProvider(s)
    return _provider


def reset_embedding_provider() -> None:
    """Test hook."""
    global _provider
    _provider = None
    global _warned
    _warned = False


def embedding_text(content: str, evidence: str = "") -> str:
    """What actually gets embedded.

    Stored content is machine-formatted ("finance.household_income: 40000"),
    while students ask in prose. Splitting the field key into words and adding
    the evidence span — the student's own phrasing — gives the vector both the
    structured meaning and the natural-language form of the same fact.
    """
    readable = (content or "").replace(".", " ").replace("_", " ")
    return f"{readable} {(evidence or '')}".strip()[:2000]
