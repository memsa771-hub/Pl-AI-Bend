"""A recall budget below the embedding timeout can never succeed.

Recall embeds the query before it can search, so a budget at or under the
embedding timeout guarantees the deadline fires first. Both failures are
swallowed and recall drops to keyword matching, which looks like working
software — measured here, EMBEDDING_TIMEOUT_SECONDS=0.6 against a provider
answering in ~1.8s meant semantic recall never once ran.
"""

from __future__ import annotations

import pytest

from pai.config import Settings, get_settings


def _settings(**kw) -> Settings:
    return get_settings().model_copy(update=kw)


def test_the_shipped_misconfiguration_is_rejected():
    """The exact values that were running: 0.6s against a ~2s round trip.

    These pass a budget-vs-timeout check (0.9 > 0.6 is consistent), which is
    why the relationship alone is not enough — the absolute value is what
    made every call fail.
    """
    with pytest.raises(ValueError, match="EMBEDDING_TIMEOUT_SECONDS"):
        Settings.model_validate(
            _settings(
                enable_semantic_embeddings=True,
                embedding_timeout_seconds=0.6,
                memory_recall_budget_seconds=0.9,
            ).model_dump()
        )


def test_budget_below_embedding_timeout_is_rejected():
    with pytest.raises(ValueError, match="MEMORY_RECALL_BUDGET_SECONDS"):
        Settings.model_validate(
            _settings(
                enable_semantic_embeddings=True,
                embedding_timeout_seconds=6.0,
                memory_recall_budget_seconds=0.9,
            ).model_dump()
        )


def test_equal_values_are_rejected():
    """Equal leaves zero room for the search that follows the embedding."""
    with pytest.raises(ValueError, match="MEMORY_RECALL_BUDGET_SECONDS"):
        Settings.model_validate(
            _settings(
                enable_semantic_embeddings=True,
                embedding_timeout_seconds=3.0,
                memory_recall_budget_seconds=3.0,
            ).model_dump()
        )


def test_budget_above_embedding_timeout_is_accepted():
    ok = Settings.model_validate(
        _settings(
            enable_semantic_embeddings=True,
            embedding_timeout_seconds=6.0,
            memory_recall_budget_seconds=8.0,
        ).model_dump()
    )
    assert ok.memory_recall_budget_seconds > ok.embedding_timeout_seconds


def test_lexical_only_deployments_are_left_alone():
    """Turning embeddings off is a deliberate choice, not a misconfiguration."""
    ok = Settings.model_validate(
        _settings(
            enable_semantic_embeddings=False,
            embedding_timeout_seconds=0.1,
            memory_recall_budget_seconds=0.5,
        ).model_dump()
    )
    assert ok.enable_semantic_embeddings is False


def test_recall_budget_has_no_hardcoded_ceiling():
    """le=2 made the fix impossible from .env: a deployment far from the
    provider could not raise the budget past two seconds at all."""
    ok = Settings.model_validate(
        _settings(
            embedding_timeout_seconds=6.0, memory_recall_budget_seconds=15.0
        ).model_dump()
    )
    assert ok.memory_recall_budget_seconds == 15.0


def test_shipped_defaults_are_self_consistent():
    live = get_settings()
    if live.enable_semantic_embeddings:
        assert live.memory_recall_budget_seconds > live.embedding_timeout_seconds


def test_unreachable_rate_limit_timeout_is_rejected():
    """1.0s against a counter upsert that measures ~1.7s.

    consume() fails open, so this did not break requests — it stopped
    enforcing every limit, daily LLM spend caps included, while logging a
    handled warning.
    """
    with pytest.raises(ValueError, match="RATE_LIMIT_BACKEND_TIMEOUT_SECONDS"):
        Settings.model_validate(
            _settings(
                enable_rate_limits=True, rate_limit_backend_timeout_seconds=1.0
            ).model_dump()
        )


def test_rate_limit_timeout_remains_bounded_for_request_latency():
    with pytest.raises(ValueError, match="less than or equal to 5"):
        Settings.model_validate(
            _settings(rate_limit_backend_timeout_seconds=15.0).model_dump()
        )


def test_disabled_limits_skip_the_check():
    ok = Settings.model_validate(
        _settings(
            enable_rate_limits=False, rate_limit_backend_timeout_seconds=0.1
        ).model_dump()
    )
    assert ok.enable_rate_limits is False
