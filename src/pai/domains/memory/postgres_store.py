from __future__ import annotations

import hashlib
import logging
import time
import uuid
from time import perf_counter
from typing import Any

from agentspan.agents.semantic_memory import MemoryEntry, MemoryStore
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pai.domains.memory.models import SemanticMemoryRow
from pai.platform.latency import record, span

logger = logging.getLogger(__name__)

# Floor for rescaled similarity: the least-close candidate of a set is still a
# vector-search hit, so it must not score as though it were unrelated.
# Measured against scripts/eval_memory_recall.py: 0.0 scores a lone candidate as
# irrelevant, 0.25 compresses the range enough to cost top-3 accuracy. 0.10 holds
# top-3 at its best while keeping the degenerate case safe.
_RESCALE_FLOOR = 0.10


class AsyncPostgresMemoryStore:
    """Async Postgres store used by PersonMemoryService (AgentSpan-compatible entries)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        person_id: uuid.UUID,
    ) -> None:
        self._session_factory = session_factory
        self._person_id = person_id

    async def add(self, entry: MemoryEntry) -> str:
        entry_id = entry.id or hashlib.sha256(
            f"{entry.content}{time.time()}".encode()
        ).hexdigest()[:16]
        created_at = entry.created_at or time.time()
        async with self._session_factory() as session:
            row = SemanticMemoryRow(
                id=uuid.uuid4(),
                person_id=self._person_id,
                content=entry.content,
                entry_metadata=dict(entry.metadata or {}),
                external_id=entry_id,
            )
            session.add(row)
            await session.commit()
        entry.id = entry_id
        entry.created_at = created_at
        return entry_id

    async def search(self, query: str, top_k: int = 5, *, mode: str = "fast") -> list[MemoryEntry]:
        from pai.config import get_settings

        settings = get_settings()
        scored_rows = await self._vector_candidates(query, settings)
        if scored_rows is not None:
            # Vector search narrowed by meaning; structural signals
            # (importance / stability / recency / claim penalty) still decide order.
            return _rank_entries(query, scored_rows, top_k, mode=mode, semantic=True)
        # Cap rows before Python ranking — full-table load does not scale.
        scan_limit = max(top_k, settings.semantic_memory_scan_limit)
        async with self._session_factory() as session:
            result = await session.execute(
                select(SemanticMemoryRow)
                .where(
                    SemanticMemoryRow.person_id == self._person_id,
                    or_(
                        SemanticMemoryRow.memory_key.is_(None),
                        SemanticMemoryRow.status.in_(("active", "candidate")),
                    ),
                )
                .order_by(SemanticMemoryRow.created_at.desc())
                .limit(scan_limit)
            )
            rows = list(result.scalars().all())
        return _rank_entries(query, rows, top_k, mode=mode)

    async def _vector_candidates(
        self, query: str, settings
    ) -> list[tuple[SemanticMemoryRow, float]] | None:
        """Nearest neighbours by meaning as (row, similarity), or None to fall
        back to lexical ranking.

        Similarity is 1 - cosine_distance, so 1.0 is identical meaning and 0.0
        is unrelated. Returning the real number (not just the ordering) lets
        rank_score weigh *how* relevant a memory is against how settled it is.
        """
        from pai.platform.llm.embeddings import get_embedding_provider

        provider = get_embedding_provider(settings)
        if provider is None:
            return None
        with span("memory_embedding"):
            vectors = await provider.embed([query])
        if not vectors:
            return None
        vector_started = perf_counter()
        try:
            distance = SemanticMemoryRow.embedding.cosine_distance(vectors[0])
            async with self._session_factory() as session:
                result = await session.execute(
                    select(SemanticMemoryRow, distance.label("distance"))
                    .where(
                        SemanticMemoryRow.person_id == self._person_id,
                        SemanticMemoryRow.embedding.isnot(None),
                        or_(
                            SemanticMemoryRow.memory_key.is_(None),
                            SemanticMemoryRow.status.in_(("active", "candidate")),
                        ),
                    )
                    .order_by(distance)
                    .limit(settings.embedding_candidate_limit)
                )
                # cosine_distance is 0..2; clamp so similarity stays within 0..1.
                rows = [
                    (row, max(0.0, min(1.0, 1.0 - float(dist))))
                    for row, dist in result.all()
                ]
        except Exception:
            # Column or extension missing (migration not applied yet).
            logger.exception("Vector recall failed")
            return None
        finally:
            record("memory_vector_search", vector_started)
        # Nothing embedded yet — let lexical ranking answer instead of returning nothing.
        return rows or None

    async def delete(self, memory_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(SemanticMemoryRow).where(
                    SemanticMemoryRow.person_id == self._person_id,
                    SemanticMemoryRow.external_id == memory_id,
                )
            )
            await session.commit()
            return (result.rowcount or 0) > 0

    async def clear(self) -> None:
        async with self._session_factory() as session:
            await session.execute(
                delete(SemanticMemoryRow).where(
                    SemanticMemoryRow.person_id == self._person_id
                )
            )
            await session.commit()

    async def list_all(self) -> list[MemoryEntry]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(SemanticMemoryRow).where(
                    SemanticMemoryRow.person_id == self._person_id
                )
            )
            rows = list(result.scalars().all())
        return [_row_to_entry(r) for r in rows]


class InProcessMemoryStore(MemoryStore):
    """AgentSpan MemoryStore for tests / ephemeral sessions."""

    def __init__(self) -> None:
        self._memories: dict[str, MemoryEntry] = {}

    def add(self, entry: MemoryEntry) -> str:
        if not entry.id:
            entry.id = hashlib.sha256(f"{entry.content}{time.time()}".encode()).hexdigest()[:16]
        if not entry.created_at:
            entry.created_at = time.time()
        self._memories[entry.id] = entry
        return entry.id

    def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        query_words = set(query.lower().split())
        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._memories.values():
            entry_words = set(entry.content.lower().split())
            if not query_words or not entry_words:
                score = 0.0
            else:
                intersection = query_words & entry_words
                union = query_words | entry_words
                score = len(intersection) / len(union) if union else 0.0
            scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for score, entry in scored[:top_k] if score > 0]

    def delete(self, memory_id: str) -> bool:
        return self._memories.pop(memory_id, None) is not None

    def clear(self) -> None:
        self._memories.clear()

    def list_all(self) -> list[MemoryEntry]:
        return list(self._memories.values())


def _row_to_entry(row: SemanticMemoryRow) -> MemoryEntry:
    meta: dict[str, Any] = dict(row.entry_metadata or {})
    return MemoryEntry(
        id=row.external_id,
        content=row.content,
        metadata=meta,
        created_at=row.created_at.timestamp() if row.created_at else 0.0,
    )


def _rank_entries(
    query: str,
    rows: list[SemanticMemoryRow] | list[tuple[SemanticMemoryRow, float]],
    top_k: int,
    *,
    mode: str = "fast",
    semantic: bool = False,
) -> list[MemoryEntry]:
    """Order candidates.

    When `semantic` is set, `rows` are (row, similarity) pairs from vector
    search: they already passed a meaning filter, so the lexical word-overlap
    requirement must not drop them, and the real similarity feeds the blend.
    """
    from pai.domains.memory.formation import format_for_recall, rank_score, record_from_row

    # Cosine similarities for one query sit in a narrow band, and the absolute
    # value carries little meaning — what matters is which of these candidates
    # is closest. Stretch the set across a floor..1.0 window so relevance can
    # separate them; without this the spread is too small to outweigh
    # importance.
    #
    # The floor matters: a plain min-max pins the worst candidate at exactly 0,
    # and with a single candidate (or near-identical similarities) it would pin
    # *every* candidate at 0 — scoring a strong match as irrelevant and handing
    # the ordering back to importance, which is the bug this rescale exists to
    # prevent.
    span_lo = span = 0.0
    if semantic and rows:
        sims = [sim for _row, sim in rows]
        span_lo = min(sims)
        # A zero spread means every candidate is equally relevant, so they all
        # land on the floor and structure decides — which is the right answer,
        # not a case to skip. Skipping would pass raw ~0.4 similarities through
        # and hand ordering back to importance.
        span = (max(sims) - span_lo) or 1.0

    scored: list[tuple[float, MemoryEntry]] = []
    for item in rows:
        row, similarity = item if semantic else (item, None)
        if semantic:
            similarity = _RESCALE_FLOOR + (1.0 - _RESCALE_FLOOR) * (
                (similarity - span_lo) / span
            )
        record = record_from_row(row)
        score = rank_score(query, record, semantic_similarity=similarity)
        if score <= 0:
            continue
        entry = _row_to_entry(row)
        if row.memory_key:
            entry.content = format_for_recall(record, mode=mode)
        scored.append((score, entry))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _score, entry in scored[:top_k]]
