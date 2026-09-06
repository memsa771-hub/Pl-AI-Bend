"""Backfill embeddings for memories written before the vector column existed.

Idempotent: only touches rows where embedding IS NULL, so it is safe to re-run
after a partial failure or a model change (clear embedding to force a redo).

    python scripts/backfill_memory_embeddings.py [--batch 100] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

# Import the model registry before touching mappers: SemanticMemoryRow has a
# foreign key to persons, which must be registered for SQLAlchemy to resolve it.
import pai.domains.student.person.models  # noqa: F401
from pai.config import get_settings
from pai.domains.memory.models import SemanticMemoryRow
from pai.platform.database.db import get_session_factory
from pai.platform.llm.embeddings import embedding_text, get_embedding_provider


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, default=100, help="rows per API call")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    provider = get_embedding_provider(settings)
    if provider is None:
        print("Embeddings are disabled or OPENAI_API_KEY is unset — nothing to do.")
        return 1

    factory = get_session_factory(settings)
    async with factory() as session:
        total = await session.scalar(
            select(func.count()).select_from(SemanticMemoryRow)
        )
        missing = await session.scalar(
            select(func.count())
            .select_from(SemanticMemoryRow)
            .where(SemanticMemoryRow.embedding.is_(None))
        )
    print(f"memories: {total}   without embedding: {missing}   model: {settings.embedding_model}")
    if not missing:
        print("Nothing to backfill.")
        return 0
    if args.dry_run:
        print("(dry run — no writes)")
        return 0

    done = 0
    failed = 0
    while True:
        async with factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(SemanticMemoryRow)
                        .where(SemanticMemoryRow.embedding.is_(None))
                        .limit(args.batch)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            texts = [
                embedding_text(r.content, (r.formation or {}).get("evidence", ""))
                for r in rows
            ]
            vectors = await provider.embed(texts)
            if not vectors or len(vectors) != len(rows):
                failed += len(rows)
                print(f"  batch failed ({len(rows)} rows) — stopping")
                break
            for row, vector in zip(rows, vectors):
                row.embedding = vector
                row.embedding_model = settings.embedding_model
            await session.commit()
            done += len(rows)
            print(f"  embedded {done}/{missing}")

    print(f"done: {done} embedded, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
