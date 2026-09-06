"""Measure recall quality: does the counselor get the right memory back?

Runs labelled questions against every student who has enough memories and
reports where the expected fact lands. A query is skipped for a student who
has no such fact stored, so the score reflects retrieval, not coverage.

Read-only — safe to run against production.

    python scripts/eval_memory_recall.py [--min-memories 8] [--verbose]

Re-run after changing the ranking blend, the embedding model, or what gets
embedded. Compare the top-1 line; a change that lowers it is a regression
however sensible it looked.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

# Registers the mappers SemanticMemoryRow's foreign keys point at.
import pai.domains.student.person.models  # noqa: F401
from pai.config import get_settings
from pai.domains.memory.service import PersonMemoryService
from pai.platform.database.db import get_session_factory
from pai.platform.llm.embeddings import get_embedding_provider

# (question, substrings that would satisfy a counselor asking it)
CASES: list[tuple[str, list[str]]] = [
    ("which country am I aiming for?", ["study_country", "preferred_regions", "mobility"]),
    ("tell me about my grades", ["test_scores", "marks", "gpa", "education.stream"]),
    ("what do I want to study?", ["career_interest", "stream", "program", "field"]),
    ("what are my chances of getting in?", ["test_scores", "education", "universit", "marks"]),
    ("where do I live?", ["location", "city", "current_country", "nationality"]),
    ("how will I pay for this?", ["finance", "funding", "budget", "scholarship", "income"]),
    ("what universities am I considering?", ["target_universit", "universit"]),
    ("what is my education background?", ["education", "degree", "highest_level", "program"]),
    ("when do I want to start?", ["admission_cycle", "intake", "year"]),
    ("what work experience do I have?", ["career", "work_history", "experience", "skills"]),
]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-memories", type=int, default=8)
    parser.add_argument("--verbose", action="store_true", help="print every miss")
    args = parser.parse_args()

    settings = get_settings()
    # Without a provider, recall silently falls back to lexical ranking and this
    # prints a normal-looking report for a path it is not measuring — while the
    # blend weight and rescale floor both cite these numbers.
    if get_embedding_provider(settings) is None:
        print(
            "Embeddings are unavailable (OPENAI_API_KEY unset or "
            "ENABLE_SEMANTIC_EMBEDDINGS off) — this would measure lexical recall.",
            file=sys.stderr,
        )
        return 1
    factory = get_session_factory(settings)

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT person_id, count(*) c FROM person_semantic_memories
                    WHERE status IN ('active','candidate')
                    GROUP BY 1 HAVING count(*) >= :n ORDER BY c DESC
                    """
                ),
                {"n": args.min_memories},
            )
        ).fetchall()

    if not rows:
        print(f"No student has {args.min_memories}+ memories yet — nothing to measure.")
        return 0

    print(f"{len(rows)} students x {len(CASES)} questions\n")
    top1 = top3 = scored = skipped = 0
    per_query: dict[str, list[int]] = {q: [0, 0] for q, _ in CASES}

    for person_id, _count in rows:
        memory = PersonMemoryService(settings, person_id, session_factory=factory)
        async with factory() as session:
            corpus = (
                await session.execute(
                    text(
                        """
                        SELECT string_agg(
                            coalesce(formation->>'field_key','') || ' ' || content, ' ')
                        FROM person_semantic_memories
                        WHERE person_id = :p AND status IN ('active','candidate')
                        """
                    ),
                    {"p": person_id},
                )
            ).scalar() or ""
        corpus = corpus.lower()

        for query, wanted in CASES:
            # Only fair to score a question this student could actually answer.
            if not any(w in corpus for w in wanted):
                skipped += 1
                continue
            recalled = await memory.recall(query)
            lines = [
                line.strip(" -")
                for line in (recalled or "").splitlines()
                if line.strip() and not line.startswith("Relevant")
            ]
            rank = next(
                (
                    i + 1
                    for i, line in enumerate(lines)
                    if any(w in line.lower() for w in wanted)
                ),
                None,
            )
            scored += 1
            per_query[query][1] += 1
            if rank == 1:
                top1 += 1
            if rank and rank <= 3:
                top3 += 1
                per_query[query][0] += 1
            elif args.verbose:
                got = lines[0][:70] if lines else "(nothing)"
                print(f"  MISS [{str(person_id)[:8]}] {query}\n        got: {got}")

    if not scored:
        print("No scorable query/student pairs.")
        return 0

    print(f"\nscored {scored} pairs ({skipped} skipped — no such fact stored)")
    print(f"  top-1 : {top1:3}/{scored}  ({100 * top1 / scored:.0f}%)")
    print(f"  top-3 : {top3:3}/{scored}  ({100 * top3 / scored:.0f}%)")
    print("\nby question (top-3 hit rate):")
    for query, (hits, attempts) in sorted(
        per_query.items(), key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 1
    ):
        if attempts:
            print(f"  {100 * hits / attempts:3.0f}%  ({hits}/{attempts})  {query}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
