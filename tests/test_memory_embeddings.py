"""Semantic memory embeddings: ranking blend, fallback, and provider safety.

These lock the properties that keep recall trustworthy when embeddings are
unavailable or misconfigured — no network and no database required.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pai.config import Settings, get_settings
from pai.domains.memory.formation import MemoryRecord, rank_score
from pai.platform.llm.embeddings import (
    OpenAIEmbeddingProvider,
    embedding_text,
    get_embedding_provider,
    reset_embedding_provider,
)


def _row(**kw):
    """Detached SemanticMemoryRow — ranking never touches the session."""
    import uuid

    from pai.domains.memory.models import SemanticMemoryRow

    base = dict(
        id=uuid.uuid4(),
        person_id=uuid.uuid4(),
        content="application.study_country: DE",
        memory_key="semantic:application.study_country",
        kind="semantic",
        status="active",
        version=1,
        external_id=uuid.uuid4().hex[:16],
        last_confirmed_at=datetime.now(UTC),
        formation={"importance": 0.88, "confidence": 0.9, "stability": 0.5},
        entry_metadata={},
    )
    base.update(kw)
    return SemanticMemoryRow(**base)


def _record(**kw) -> MemoryRecord:
    base = dict(
        memory_key="semantic:finance.household_income",
        content="finance.household_income: 40000/month",
        kind="semantic",
        status="active",
        version=1,
        confidence=0.8,
        importance=0.88,
        recurrence=1,
        stability=0.5,
        evidence_count=1,
        assertion_status="explicit",
        evidence="my mother earns about 40k a month",
        last_confirmed_at=datetime.now(UTC),
    )
    base.update(kw)
    return MemoryRecord(**base)


# ── ranking ────────────────────────────────────────────────────────────────


def test_semantic_similarity_replaces_word_overlap():
    """The case embeddings exist for: no shared words, still recalled."""
    record = _record()
    query = "how can I afford this?"
    # Lexical path drops it — that is the bug being fixed.
    assert rank_score(query, record) == -1.0
    # Vector path keeps it, because meaning matched.
    assert rank_score(query, record, semantic_similarity=0.41) > 0


def test_similarity_is_weighted_not_just_ordered():
    """A closer match must score higher than a distant one, same record."""
    record = _record()
    near = rank_score("q", record, semantic_similarity=0.9)
    far = rank_score("q", record, semantic_similarity=0.2)
    assert near > far, "similarity must affect the score, not only the order"


def test_structural_signals_still_apply_under_vector_search():
    """Vector search selects candidates; importance still separates them."""
    important = _record(importance=0.95)
    trivial = _record(importance=0.2)
    same_similarity = 0.5
    assert rank_score("q", important, semantic_similarity=same_similarity) > rank_score(
        "q", trivial, semantic_similarity=same_similarity
    )


def test_relevance_outranks_importance_on_the_vector_path():
    """The bug this weighting fixes.

    Cosine similarities sit in a narrow band, so with the lexical weights the
    single highest-importance memory won every query regardless of what was
    asked — "which country am I aiming for?" returned education.highest_level.
    A clearly more relevant memory must beat a merely more important one.
    """
    relevant = _record(memory_key="semantic:application.study_country", importance=0.55)
    important_but_off_topic = _record(
        memory_key="semantic:education.highest_level", importance=0.95
    )
    assert rank_score("q", relevant, semantic_similarity=1.0) > rank_score(
        "q", important_but_off_topic, semantic_similarity=0.0
    )


def test_importance_still_breaks_ties_at_equal_relevance():
    """Relevance leads, but structure must still decide between close matches."""
    high = _record(importance=0.95)
    low = _record(importance=0.42)
    assert rank_score("q", high, semantic_similarity=0.5) > rank_score(
        "q", low, semantic_similarity=0.5
    )


def test_single_candidate_is_not_scored_as_irrelevant():
    """A plain min-max rescale pins the only candidate at relevance 0.

    Vector search returning one row means one row matched — scoring it as
    though it were unrelated (and handing the ordering back to importance)
    is exactly the bug the rescale exists to prevent.
    """
    from pai.domains.memory.postgres_store import _rank_entries

    row = _row(content="finance.household_income: 40000/month")
    entries = _rank_entries("how can I afford this?", [(row, 0.95)], 5, semantic=True)
    assert len(entries) == 1, "a lone strong match must survive ranking"


def test_near_identical_similarities_do_not_collapse():
    """When everything is equally close, no candidate may be zeroed out."""
    from pai.domains.memory.postgres_store import _rank_entries

    rows = [(_row(content=f"application.field_{i}: v"), 0.400 + i * 0.001) for i in range(4)]
    entries = _rank_entries("q", rows, 10, semantic=True)
    assert len(entries) == 4


def test_lexical_path_keeps_its_original_weighting():
    """Jaccard spreads far wider than cosine; its balance is unchanged."""
    record = _record(importance=0.88, stability=0.5, confidence=0.8)
    # 0.40*jaccard + 0.25*imp + 0.20*stab + 0.10*recency + 0.05*conf
    score = rank_score("finance household income 40000", record)
    assert 0.0 < score < 1.0


def test_unverified_claim_penalty_survives_vector_path():
    """A rejected claim must not outrank settled truth just because it is close."""
    truth = _record(memory_key="semantic:application.study_country")
    claim = _record(memory_key="claim:application.study_country")
    # Give the claim the *better* similarity; the penalty must still hold it down.
    claim_score = rank_score("q", claim, semantic_similarity=0.9)
    truth_score = rank_score("q", truth, semantic_similarity=0.75)
    assert truth_score > claim_score


def test_superseded_and_ephemeral_never_recalled():
    for status in ("superseded", "ephemeral"):
        assert rank_score("q", _record(status=status), semantic_similarity=1.0) <= 0


def test_similarity_is_clamped():
    """Out-of-range similarity must not produce a runaway score."""
    record = _record()
    assert rank_score("q", record, semantic_similarity=5.0) == pytest.approx(
        rank_score("q", record, semantic_similarity=1.0)
    )
    assert rank_score("q", record, semantic_similarity=-3.0) > 0


# ── what gets embedded ─────────────────────────────────────────────────────


def test_embedding_text_makes_field_keys_searchable():
    """Dotted keys are split so prose queries can match them."""
    text = embedding_text("finance.household_income: 40000/month", "mother earns 40k")
    assert "household income" in text  # underscore and dot flattened
    assert "mother earns 40k" in text  # student's own words retained


def test_embedding_text_is_bounded():
    assert len(embedding_text("x" * 9000, "y" * 9000)) <= 2000


# ── provider safety ────────────────────────────────────────────────────────


def _settings(**kw) -> Settings:
    return get_settings().model_copy(update=kw)


def test_provider_is_none_without_api_key():
    """No key must disable embeddings, not raise — recall falls back."""
    reset_embedding_provider()
    assert get_embedding_provider(_settings(openai_api_key="")) is None
    reset_embedding_provider()


def test_provider_is_none_when_disabled():
    reset_embedding_provider()
    assert (
        get_embedding_provider(
            _settings(enable_semantic_embeddings=False, openai_api_key="sk-test")
        )
        is None
    )
    reset_embedding_provider()


async def test_embed_returns_none_on_empty_input():
    provider = OpenAIEmbeddingProvider(_settings(openai_api_key="sk-test"))
    assert await provider.embed(["", "   "]) is None


async def test_embed_returns_none_when_client_unavailable():
    """A missing key must yield None rather than an exception mid-turn."""
    provider = OpenAIEmbeddingProvider(_settings(openai_api_key=""))
    assert await provider.embed(["anything"]) is None


async def test_dimension_mismatch_rejects_batch(monkeypatch):
    """A wrong-width vector cannot be stored; refuse it before the DB does."""

    class _Datum:
        def __init__(self, index, embedding):
            self.index, self.embedding = index, embedding

    class _Response:
        def __init__(self, data):
            self.data, self.usage = data, None

    provider = OpenAIEmbeddingProvider(_settings(openai_api_key="sk-test"))
    provider.dimensions = 1536

    class _Embeddings:
        async def create(self, **kw):
            return _Response([_Datum(0, [0.1] * 384)])  # wrong width

    class _Client:
        embeddings = _Embeddings()

    provider._client = _Client()
    assert await provider.embed(["text"]) is None


async def test_results_are_ordered_by_api_index(monkeypatch):
    """Vectors must line up with their input rows even if the API reorders."""

    class _Datum:
        def __init__(self, index, embedding):
            self.index, self.embedding = index, embedding

    class _Response:
        def __init__(self, data):
            self.data, self.usage = data, None

    provider = OpenAIEmbeddingProvider(_settings(openai_api_key="sk-test"))
    provider.dimensions = 2

    class _Embeddings:
        async def create(self, **kw):
            # deliberately out of order
            return _Response([_Datum(1, [9.0, 9.0]), _Datum(0, [1.0, 1.0])])

    class _Client:
        embeddings = _Embeddings()

    provider._client = _Client()
    vectors = await provider.embed(["first", "second"])
    assert vectors == [[1.0, 1.0], [9.0, 9.0]]
