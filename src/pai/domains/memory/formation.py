"""Memory formation: what an observation *means* in the student's long-term story.

Vault stays current structured truth. Journey stays goals. Events stay evidence.
This layer upserts explainable memories: strengthen on repeat, version on change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pai.domains.memory.models import SemanticMemoryRow
from pai.domains.student.vault.catalog import get_catalog_field
from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.evidence.assertion import assertion_of, format_observed, is_vault_eligible

logger = logging.getLogger(__name__)

Action = Literal["insert", "strengthen", "supersede", "noop"]

_SLUG = re.compile(r"[^a-z0-9]+")
_LIVE = ("active", "candidate")
# Unverified claims stay recallable but rank below settled Vault truth.
_CLAIM_RANK_PENALTY = 0.5
# How much of a vector-search score is "does this answer the question" versus
# "is this a settled, important fact". Measured against labelled queries on real
# memories: at 0.40 the structural half dominated and one high-importance memory
# won everything; 0.60 tripled top-1 hits.
_SEMANTIC_RELEVANCE_WEIGHT = 0.60


@dataclass
class MemoryDraft:
    memory_key: str
    content: str
    kind: str
    status: str
    confidence: float
    importance: float
    assertion_status: str
    evidence: str = ""
    belongs_to: str = "profile"
    related: list[str] = field(default_factory=list)
    field_key: str | None = None
    value: Any = None


@dataclass
class MemoryRecord:
    memory_key: str
    content: str
    kind: str
    status: str
    version: int
    confidence: float
    importance: float
    recurrence: int
    stability: float
    evidence_count: int
    assertion_status: str
    evidence: str = ""
    belongs_to: str = "profile"
    related: list[str] = field(default_factory=list)
    previous_content: str | None = None
    field_key: str | None = None
    last_confirmed_at: datetime | None = None
    valid_until: datetime | None = None


def memory_key_for(candidate: VaultCandidate) -> str:
    if is_vault_eligible(candidate):
        return f"semantic:{candidate.field_key}"
    who = (candidate.attributed_to or "student").strip().lower() or "student"
    body = candidate.value
    if not isinstance(body, str):
        body = json.dumps(body, sort_keys=True, default=str)
    kind = candidate.fact_type or candidate.field_key or "fact"
    return f"observed:{who}:{_slug(str(kind))}:{_slug(str(body))}"


def importance_of(candidate: VaultCandidate) -> float:
    key = candidate.field_key or ""
    status = assertion_of(candidate)
    if status == "hypothetical":
        return 0.32
    if key.startswith("identity.") or key == "education.highest_level":
        return 0.95
    if key.startswith(("application.", "finance.")) or key == "mobility.preferred_regions":
        return 0.88 if status != "uncertain" else 0.55
    if key.startswith("education.") or key.startswith("career."):
        return 0.82
    if status == "negated":
        return 0.72
    if not is_vault_eligible(candidate):
        return 0.42
    return 0.55


def drafts_from_turn(
    *,
    accepted: list[VaultCandidate] | None = None,
    pending: list[VaultCandidate] | None = None,
    conflicts: list[VaultCandidate] | None = None,
    observed: list[VaultCandidate] | None = None,
) -> list[MemoryDraft]:
    drafts: list[MemoryDraft] = []
    for row in accepted or []:
        draft = _draft_from_candidate(row, status="active", kind=_kind_for(row))
        if draft is not None:
            drafts.append(draft)
    for row in pending or []:
        draft = _draft_from_candidate(row, status="candidate", kind=_kind_for(row))
        if draft is not None:
            drafts.append(draft)
    for row in conflicts or []:
        draft = _draft_from_candidate(
            row, status="candidate", kind="observed", key_prefix="claim"
        )
        if draft is not None:
            drafts.append(draft)
    for row in observed or []:
        draft = _draft_from_candidate(row, status=_observed_status(row), kind="observed")
        if draft is not None:
            drafts.append(draft)
    return _link_turn(merge_drafts(drafts))


def merge_drafts(drafts: list[MemoryDraft]) -> list[MemoryDraft]:
    best: dict[str, MemoryDraft] = {}
    for row in drafts:
        prev = best.get(row.memory_key)
        if prev is None:
            best[row.memory_key] = row
            continue
        if row.confidence >= prev.confidence:
            best[row.memory_key] = row
    return list(best.values())


def apply_draft(
    existing: MemoryRecord | None,
    draft: MemoryDraft,
    *,
    now: datetime | None = None,
) -> tuple[Action, MemoryRecord, MemoryRecord | None]:
    """Pure upsert: strengthen same meaning, version when the meaning changes."""
    stamp = now or datetime.now(UTC)
    incoming = _record_from_draft(draft, now=stamp)
    if existing is None:
        return "insert", incoming, None
    if _norm(existing.content) == _norm(draft.content):
        recurrence = existing.recurrence + 1
        status = existing.status
        if recurrence >= 3 and status == "candidate":
            status = "active"
        if draft.assertion_status == "explicit" and draft.confidence >= 0.9:
            status = "active" if existing.status != "superseded" else status
        if existing.status == "active":
            status = "active"
        updated = replace(
            existing,
            confidence=min(0.99, max(existing.confidence, draft.confidence) + 0.05),
            importance=max(existing.importance, draft.importance),
            recurrence=recurrence,
            stability=min(0.95, 1.0 - 1.0 / (recurrence + 1)),
            evidence_count=existing.evidence_count + 1,
            last_confirmed_at=stamp,
            evidence=draft.evidence or existing.evidence,
            related=_union(existing.related, draft.related),
            assertion_status=draft.assertion_status or existing.assertion_status,
            kind=draft.kind if draft.kind == "decision" else existing.kind,
            status=status,
            belongs_to=draft.belongs_to or existing.belongs_to,
        )
        return "strengthen", updated, None
    superseded = replace(existing, status="superseded", valid_until=stamp)
    incoming = replace(
        incoming,
        version=existing.version + 1,
        previous_content=existing.content,
        related=_union(existing.related, draft.related),
        stability=0.25,
        belongs_to=draft.belongs_to or existing.belongs_to,
    )
    return "supersede", incoming, superseded


def is_unverified_claim(record: MemoryRecord) -> bool:
    """A value the gates refused because it contradicts an active Vault value.

    Written by drafts_from_turn(conflicts=...) under a `claim:` key so the
    counselor can raise it. It is an open question, never settled truth.
    """
    return (record.memory_key or "").startswith("claim:")


def format_for_recall(record: MemoryRecord, *, mode: str = "fast") -> str:
    if is_unverified_claim(record):
        head = (
            f"[UNCONFIRMED CLAIM — contradicts the stored value; "
            f"ask the student, do not treat as true] {record.content}"
        )
    else:
        head = f"[{record.kind}/{record.status} ×{record.recurrence}] {record.content}"
    extra: list[str] = []
    if record.previous_content:
        extra.append(f"previously: {record.previous_content[:160]}")
    if mode == "audit" and record.evidence:
        extra.append(f'evidence="{record.evidence[:160]}"')
    if record.belongs_to and record.belongs_to != "profile":
        extra.append(f"belongs_to={record.belongs_to}")
    if extra:
        return head + " — " + "; ".join(extra)
    return head


def rank_score(
    query: str,
    record: MemoryRecord,
    *,
    now: datetime | None = None,
    semantic_similarity: float | None = None,
) -> float:
    """Blend relevance with how settled a memory is.

    `semantic_similarity` is cosine similarity from vector search (0..1, higher
    is closer in meaning). When present it replaces word overlap: those rows
    were selected by meaning, so requiring shared words would discard exactly
    the matches embeddings exist to find.
    """
    if record.status == "superseded":
        return -1.0
    if record.importance < 0.15 or record.status == "ephemeral":
        return -1.0
    semantic = semantic_similarity is not None
    if semantic:
        relevance = min(1.0, max(0.0, float(semantic_similarity)))
    else:
        relevance = _jaccard(
            query,
            " ".join(
                [
                    record.content,
                    record.evidence,
                    record.memory_key.replace(":", " ").replace(".", " ").replace("_", " "),
                    record.field_key or "",
                ]
            ),
        )
        if relevance <= 0:
            return -1.0
    recency = _recency(record.last_confirmed_at, now)
    structure = (
        0.55 * record.importance
        + 0.25 * record.stability
        + 0.12 * recency
        + 0.08 * record.confidence
    )
    if semantic:
        # Cosine similarities sit in a narrow band (~0.28-0.48 in practice), so
        # the lexical weights let importance out-vote relevance: the single
        # highest-importance memory won every query regardless of what was
        # asked. The caller normalises similarity across the candidate set;
        # relevance leads and structure breaks ties between close matches.
        score = _SEMANTIC_RELEVANCE_WEIGHT * relevance + (
            1.0 - _SEMANTIC_RELEVANCE_WEIGHT
        ) * structure
    else:
        # Jaccard spreads much wider, so the original balance still holds.
        score = (
            0.40 * relevance
            + 0.25 * record.importance
            + 0.20 * record.stability
            + 0.10 * recency
            + 0.05 * record.confidence
        )
    # An unverified claim must never outrank the Vault-backed fact it contradicts.
    # It stays recallable (the counselor should ask about it) but ranks below
    # settled truth competing for the same recall slots.
    if is_unverified_claim(record):
        score *= _CLAIM_RANK_PENALTY
    return score


async def apply_memory_drafts(
    session: AsyncSession,
    person_id: uuid.UUID,
    drafts: list[MemoryDraft],
) -> int:
    drafts = merge_drafts(drafts)
    if not drafts:
        return 0
    result = await session.execute(
        select(SemanticMemoryRow).where(
            SemanticMemoryRow.person_id == person_id,
            SemanticMemoryRow.memory_key.in_([d.memory_key for d in drafts]),
            SemanticMemoryRow.status.in_(_LIVE),
        )
    )
    by_key = {row.memory_key: row for row in result.scalars().all() if row.memory_key}
    now = datetime.now(UTC)
    written = 0
    touched: list[SemanticMemoryRow] = []
    for draft in drafts:
        existing_row = by_key.get(draft.memory_key)
        existing = record_from_row(existing_row) if existing_row is not None else None
        action, new_rec, old_rec = apply_draft(existing, draft, now=now)
        if action == "noop":
            continue
        if action == "strengthen" and existing_row is not None:
            _write_row(existing_row, new_rec)
        elif action == "supersede" and existing_row is not None and old_rec is not None:
            _write_row(existing_row, old_rec)
            created = _new_row(person_id, new_rec)
            session.add(created)
            by_key[draft.memory_key] = created
        else:
            created = _new_row(person_id, new_rec)
            session.add(created)
            by_key[draft.memory_key] = created
        touched.append(by_key[draft.memory_key])
        written += 1
    return written


async def embed_pending_memories(
    session_factory: async_sessionmaker[AsyncSession],
    person_id: uuid.UUID,
    *,
    limit: int = 50,
) -> int:
    """Embed this person's memories that still lack a vector.

    Runs on its own session AFTER the caller has committed. Embedding is an
    outbound HTTPS call; doing it inside the write transaction would hold row
    locks for the duration of a third-party request. Rows are found by
    `embedding IS NULL`, so a failure here simply leaves them for the next turn
    (or the backfill script) — they stay recallable lexically meanwhile.
    """
    from pai.config import get_settings
    from pai.platform.llm.embeddings import embedding_text, get_embedding_provider

    settings = get_settings()
    provider = get_embedding_provider(settings)
    if provider is None:
        return 0
    try:
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(SemanticMemoryRow)
                        .where(
                            SemanticMemoryRow.person_id == person_id,
                            SemanticMemoryRow.embedding.is_(None),
                        )
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return 0
            texts = [
                embedding_text(r.content, (r.formation or {}).get("evidence", ""))
                for r in rows
            ]
            vectors = await provider.embed(texts)
            if not vectors or len(vectors) != len(rows):
                return 0
            expected = settings.embedding_dimensions
            for row, vector in zip(rows, vectors):
                if len(vector) != expected:
                    logger.error(
                        "Embedding dimension mismatch: model returned %s, column expects %s",
                        len(vector),
                        expected,
                    )
                    return 0
                row.embedding = vector
                row.embedding_model = settings.embedding_model
            await session.commit()
            return len(rows)
    except Exception:
        logger.exception("Embedding memories failed (non-fatal)")
        return 0


def record_from_row(row: SemanticMemoryRow) -> MemoryRecord:
    blob = dict(row.formation or {})
    related = blob.get("related") or []
    if not isinstance(related, list):
        related = []
    return MemoryRecord(
        memory_key=row.memory_key or "",
        content=row.content,
        kind=row.kind or "note",
        status=row.status or "active",
        version=int(row.version or 1),
        confidence=float(blob.get("confidence", 0.5)),
        importance=float(blob.get("importance", 0.4)),
        recurrence=int(blob.get("recurrence", 1)),
        stability=float(blob.get("stability", 0.2)),
        evidence_count=int(blob.get("evidence_count", 1)),
        assertion_status=str(blob.get("assertion_status") or "explicit"),
        evidence=str(blob.get("evidence") or ""),
        belongs_to=str(blob.get("belongs_to") or "profile"),
        related=[str(x) for x in related],
        previous_content=blob.get("previous_content"),
        field_key=blob.get("field_key"),
        last_confirmed_at=row.last_confirmed_at,
        valid_until=row.valid_until,
    )


def _draft_from_candidate(
    candidate: VaultCandidate,
    *,
    status: str,
    kind: str,
    key_prefix: str | None = None,
) -> MemoryDraft | None:
    field = get_catalog_field(candidate.field_key)
    if field is not None and field.sensitive:
        return None
    importance = importance_of(candidate)
    if importance < 0.15:
        return None
    key = memory_key_for(candidate)
    if key_prefix:
        key = f"{key_prefix}:{candidate.field_key}:{_slug(str(candidate.value)[:40])}"
    return MemoryDraft(
        memory_key=key,
        content=_content_for(candidate),
        kind=kind,
        status=status if importance >= 0.15 else "ephemeral",
        confidence=float(candidate.confidence),
        importance=importance,
        assertion_status=assertion_of(candidate),
        evidence=(candidate.evidence_text or "")[:400],
        belongs_to=_belongs_to(candidate.field_key),
        field_key=candidate.field_key,
        value=candidate.value,
    )


def _kind_for(candidate: VaultCandidate) -> str:
    if candidate.is_correction:
        return "decision"
    return "semantic"


def _observed_status(candidate: VaultCandidate) -> str:
    status = assertion_of(candidate)
    if status in ("hypothetical", "uncertain"):
        return "candidate"
    if status == "inferred":
        return "candidate"
    return "active"


def _belongs_to(field_key: str | None) -> str:
    key = field_key or ""
    if key.startswith(("application.", "finance.", "mobility.")):
        return "goal:now"
    return "profile"


def _content_for(candidate: VaultCandidate) -> str:
    if not is_vault_eligible(candidate):
        return format_observed(candidate)
    value = candidate.value
    if not isinstance(value, str):
        value = json.dumps(value, default=str)
    status = assertion_of(candidate)
    label = candidate.field_key
    if status == "negated":
        return f"Rejected {label}: {value}"
    if status == "hypothetical":
        return f"Considering {label}: {value} (conditional)"
    if candidate.is_correction:
        return f"Updated {label}: {value}"
    return f"{label}: {value}"


def _record_from_draft(draft: MemoryDraft, *, now: datetime) -> MemoryRecord:
    return MemoryRecord(
        memory_key=draft.memory_key,
        content=draft.content[:2000],
        kind=draft.kind,
        status=draft.status,
        version=1,
        confidence=draft.confidence,
        importance=draft.importance,
        recurrence=1,
        stability=0.2,
        evidence_count=1,
        assertion_status=draft.assertion_status,
        evidence=draft.evidence,
        belongs_to=draft.belongs_to,
        related=list(draft.related),
        field_key=draft.field_key,
        last_confirmed_at=now,
    )


def _link_turn(drafts: list[MemoryDraft]) -> list[MemoryDraft]:
    keys = [row.memory_key for row in drafts]
    linked: list[MemoryDraft] = []
    for row in drafts:
        peers = [key for key in keys if key != row.memory_key][:6]
        linked.append(replace(row, related=peers) if peers else row)
    return linked


def _write_row(row: SemanticMemoryRow, record: MemoryRecord) -> None:
    row.content = record.content[:2000]
    row.memory_key = record.memory_key
    row.kind = record.kind
    row.status = record.status
    row.version = record.version
    row.last_confirmed_at = record.last_confirmed_at
    row.valid_until = record.valid_until
    row.formation = _formation_blob(record)
    meta = dict(row.entry_metadata or {})
    meta.update(
        {
            "type": "formed_memory",
            "kind": record.kind,
            "memory_key": record.memory_key,
            "status": record.status,
        }
    )
    row.entry_metadata = meta


def _new_row(person_id: uuid.UUID, record: MemoryRecord) -> SemanticMemoryRow:
    external = hashlib.sha256(
        f"{person_id}:{record.memory_key}:{record.version}".encode()
    ).hexdigest()[:16]
    return SemanticMemoryRow(
        person_id=person_id,
        content=record.content[:2000],
        memory_key=record.memory_key,
        kind=record.kind,
        status=record.status,
        version=record.version,
        last_confirmed_at=record.last_confirmed_at,
        valid_until=record.valid_until,
        formation=_formation_blob(record),
        entry_metadata={
            "type": "formed_memory",
            "kind": record.kind,
            "memory_key": record.memory_key,
            "status": record.status,
        },
        external_id=external,
    )


def _formation_blob(record: MemoryRecord) -> dict[str, Any]:
    return {
        "confidence": record.confidence,
        "importance": record.importance,
        "recurrence": record.recurrence,
        "stability": record.stability,
        "evidence_count": record.evidence_count,
        "belongs_to": record.belongs_to,
        "related": record.related,
        "previous_content": record.previous_content,
        "field_key": record.field_key,
        "assertion_status": record.assertion_status,
        "evidence": record.evidence,
    }


def _slug(text: str, n: int = 48) -> str:
    token = _SLUG.sub("_", (text or "").lower()).strip("_")
    return (token[:n] or "fact")


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _union(left: list[str], right: list[str]) -> list[str]:
    out: list[str] = []
    for item in [*left, *right]:
        if item and item not in out:
            out.append(item)
        if len(out) >= 8:
            break
    return out


def _jaccard(query: str, text: str) -> float:
    q = set(query.lower().split())
    t = set(text.lower().replace(":", " ").replace(".", " ").split())
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


def _recency(confirmed: datetime | None, now: datetime | None) -> float:
    if confirmed is None:
        return 0.4
    stamp = now or datetime.now(UTC)
    if confirmed.tzinfo is None:
        confirmed = confirmed.replace(tzinfo=UTC)
    days = max(0.0, (stamp - confirmed).total_seconds() / 86400.0)
    return max(0.05, 1.0 - min(days, 180.0) / 180.0)
