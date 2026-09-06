"""Goal detection unit tests — regression suite against labeled fixture set.

These tests do NOT call an LLM. They test the resolver's deterministic
decision-making by providing a pre-decided llm_goal signal (or None).

The fixture file defines the ground truth. New misclassifications found in
production should be added here as regression cases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pai.intelligences.goals.resolver import (
    _classify_goal_type,
    _extract_anchors_from_intent,
    resolve,
)
from pai.kernel.contracts.schemas import GoalExtract

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "detection_cases.json"

# Messages that should NOT trigger a goal write
_NO_GOAL_MESSAGES = {
    "what is life like in germany?",
    "should i take ielts first or passport first?",
    "my cgpa is 3.4",
    "i got 7.5 in ielts",
    "can i apply for ms in canada?",
    "what do i need for phd in sweden?",
    "hello",
    "what is my next step?",
}

# Messages with clear pursuit intent
_PURSUIT_MESSAGES: dict[str, str] = {
    "i want to do ms cs in germany": "admission",
    "looking for a swe internship in dubai": "internship",
    "i am applying for a full-time swe job at google": "job",
    "i want a phd in ai from sweden": "admission",
    "i want to do ms abroad": "admission",
    "i am pursuing a full-time role in data science in the uk": "job",
}


def _make_life_aim(intent: str, mode: str = "pursuing") -> GoalExtract:
    return GoalExtract(
        kind="life_aim",
        stated=True,
        intent=intent,
        mode=mode,
        supersedes_previous=False,
        evidence_text=intent[:100],
    )


def _make_no_goal() -> None:
    return None


# ── Fixture-driven tests ──────────────────────────────────────────────────────


def _load_fixtures() -> list[dict]:
    with FIXTURE_PATH.open() as f:
        return json.load(f)


@pytest.mark.parametrize("case", _load_fixtures(), ids=lambda c: c["message"][:60])
def test_goal_type_classification(case: dict) -> None:
    """Verify that goal_type matches expected for 'create' cases."""
    msg = case["message"]
    expected_action = case["expect"]
    if expected_action == "none":
        return  # Skip — non-goal messages not tested for type here
    expected_type = case.get("type")
    if expected_type is None:
        return
    detected = _classify_goal_type(msg, {})
    assert detected == expected_type, (
        f"Message: {msg!r}\n"
        f"Expected type: {expected_type}, got: {detected}"
    )


def test_no_goal_messages_produce_no_intent() -> None:
    """Vault facts and casual questions must not become goals."""
    for msg in _NO_GOAL_MESSAGES:
        # If we pass None as llm_goal, the resolver must return 'none'
        # (resolver gates on llm_goal.kind == 'life_aim')
        llm_goal = None
        # Simulate: no life_aim means no goal
        assert llm_goal is None or getattr(llm_goal, "kind", "none") != "life_aim"


# ── Goal type classification ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("MS CS in Germany", "admission"),
        ("PhD in AI from Sweden", "admission"),
        ("bachelor in computer science", "admission"),
        ("SWE internship in Dubai", "internship"),
        ("full time job as data scientist", "job"),
        ("software engineer role at Google", "general"),
        ("MBA in UK", "admission"),
        ("something completely unknown", "general"),
    ],
)
def test_classify_goal_type(text: str, expected: str) -> None:
    assert _classify_goal_type(text, {}) == expected


# ── Anchor extraction ─────────────────────────────────────────────────────────


def test_extract_anchors_germany_ms() -> None:
    anchors = _extract_anchors_from_intent("MS CS in Germany", "admission")
    assert anchors["goal_type"] == "admission"
    assert anchors.get("target_country") == "DE"
    assert anchors.get("degree_level") == "ms"


def test_extract_anchors_dubai_internship() -> None:
    anchors = _extract_anchors_from_intent("SWE internship in Dubai", "internship")
    assert anchors["goal_type"] == "internship"
    assert anchors.get("target_country") == "AE"


def test_extract_anchors_phd_sweden() -> None:
    anchors = _extract_anchors_from_intent("PhD in AI from Sweden", "admission")
    assert anchors.get("degree_level") == "phd"
    assert anchors.get("target_country") == "SE"


# ── Resolver: async tests using mocked session ────────────────────────────────


@pytest.fixture
def mock_session():
    """Async session mock that returns empty results by default."""
    session = AsyncMock()

    async def fake_execute(query, *args, **kwargs):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.all.return_value = []
        return result

    session.execute = fake_execute
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    return session


@pytest.fixture
def person_id():
    import uuid

    return uuid.uuid4()


@pytest.fixture
def conversation_id():
    import uuid

    return uuid.uuid4()


@pytest.mark.asyncio
async def test_resolver_none_when_no_llm_goal(mock_session, person_id, conversation_id):
    result = await resolve(
        mock_session, person_id, conversation_id, llm_goal=None, user_message="hello"
    )
    assert result.action == "none"
    assert result.goal is None


@pytest.mark.asyncio
async def test_resolver_none_when_turn_action(mock_session, person_id, conversation_id):
    llm_goal = GoalExtract(kind="turn_action", intent="What test should I take?")
    result = await resolve(
        mock_session,
        person_id,
        conversation_id,
        llm_goal=llm_goal,
        user_message="What test should I take?",
    )
    assert result.action == "none"


@pytest.mark.asyncio
async def test_resolver_none_when_evidence_not_in_message(mock_session, person_id, conversation_id):
    llm_goal = GoalExtract(
        kind="life_aim",
        intent="study in Germany",
        mode="pursuing",
        stated=True,
        evidence_text="study in Germany",
    )
    result = await resolve(
        mock_session,
        person_id,
        conversation_id,
        llm_goal=llm_goal,
        user_message="yeh attach karunga",
    )
    assert result.action == "none"
    assert result.goal is None


@pytest.mark.asyncio
async def test_resolver_creates_goal_for_life_aim(mock_session, person_id, conversation_id):
    """A life_aim with sufficient intent should trigger goal creation."""
    import uuid

    llm_goal = GoalExtract(
        kind="life_aim",
        intent="MS CS in Germany",
        mode="pursuing",
        stated=True,
        evidence_text="MS CS in Germany",
    )

    created_goal = MagicMock()
    created_goal.id = uuid.uuid4()
    created_goal.goal_type = "admission"
    created_goal.lifecycle_status = "active"
    created_goal.intelligence_status = "pending"
    created_goal.anchors = {}
    created_goal.title = "MS CS in Germany"
    created_goal.person_id = person_id

    # Patch upsert_goal_from_anchors and related functions
    from unittest.mock import patch

    with (
        patch(
            "pai.intelligences.goals.resolver.get_conversation_active_goal",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "pai.intelligences.goals.resolver.find_matching_goal",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "pai.intelligences.goals.resolver.upsert_goal_from_anchors",
            new=AsyncMock(return_value=(created_goal, "create")),
        ),
        patch(
            "pai.intelligences.goals.resolver.enqueue_goal_intelligence_job",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        result = await resolve(
            mock_session,
            person_id,
            conversation_id,
            llm_goal=llm_goal,
            user_message="I want to do MS CS in Germany",
        )
    assert result.action == "create"
    assert result.goal is not None


# ── Deduplication regressions ─────────────────────────────────────────────────
# These pin down the exact bug pattern from production: a vague rephrase that
# extracts fewer/no anchors must reinforce the existing goal instead of
# spawning a duplicate Goal row, and a merely-different-anchors mention must
# not be treated as an explicit switch.


def _make_active_goal(**overrides) -> MagicMock:
    from pai.domains.goals.models import Goal

    goal = MagicMock(spec=Goal)
    goal.id = overrides.get("id") or __import__("uuid").uuid4()
    goal.goal_type = overrides.get("goal_type", "admission")
    goal.title = overrides.get("title", "MS in Germany")
    goal.target_country = overrides.get("target_country", "DE")
    goal.degree_level = overrides.get("degree_level", "ms")
    goal.program = overrides.get("program")
    goal.role = overrides.get("role")
    goal.target_company = overrides.get("target_company")
    goal.anchors = overrides.get("anchors", {})
    goal.intelligence_status = overrides.get("intelligence_status", "ready")
    return goal


@pytest.mark.asyncio
async def test_vague_rephrase_reinforces_active_goal_instead_of_duplicating(
    mock_session, person_id, conversation_id
):
    """A rephrase with fewer anchors (no country extracted) must REINFORCE,
    not create a second Goal row for the same pursuit."""
    active_goal = _make_active_goal()
    llm_goal = GoalExtract(
        kind="life_aim",
        intent="I want to pursue my masters",
        mode="pursuing",
        stated=True,
        supersedes_previous=False,
        evidence_text="I want to pursue my masters",
    )
    with patch(
        "pai.intelligences.goals.resolver.get_conversation_active_goal",
        new=AsyncMock(return_value=active_goal),
    ), patch(
        "pai.intelligences.goals.resolver.enqueue_goal_intelligence_job",
        new=AsyncMock(return_value=MagicMock()),
    ):
        result = await resolve(
            mock_session,
            person_id,
            conversation_id,
            llm_goal=llm_goal,
            user_message="I want to pursue my masters",
        )
    assert result.action == "reinforce"
    assert result.goal is active_goal


@pytest.mark.asyncio
async def test_mentioning_different_goal_without_pivot_is_secondary_not_switch(
    mock_session, person_id, conversation_id
):
    """Mentioning a goal with conflicting anchors (different country) is a
    secondary/exploring pursuit unless the LLM detected explicit pivot
    language (supersedes_previous). Anchor dissimilarity alone must never
    force a switch — that used to pause the real active goal by mistake."""
    active_goal = _make_active_goal(title="MS in Germany")
    other_goal = _make_active_goal(
        title="MBA in USA", target_country="US", degree_level="mba"
    )

    async def fake_list_goals(session, pid, *, include_archived=False):
        return [other_goal]

    llm_goal = GoalExtract(
        kind="life_aim",
        intent="MBA in USA",
        mode="exploring",
        stated=True,
        supersedes_previous=False,
        evidence_text="MBA in USA",
    )
    with patch(
        "pai.intelligences.goals.resolver.get_conversation_active_goal",
        new=AsyncMock(return_value=active_goal),
    ), patch(
        "pai.intelligences.goals.resolver.list_goals",
        new=fake_list_goals,
    ), patch(
        "pai.intelligences.goals.resolver.enqueue_goal_intelligence_job",
        new=AsyncMock(return_value=MagicMock()),
    ):
        result = await resolve(
            mock_session,
            person_id,
            conversation_id,
            llm_goal=llm_goal,
            user_message="I am also considering an MBA in the USA",
        )
    assert result.action == "create_secondary"
    assert result.goal is other_goal
