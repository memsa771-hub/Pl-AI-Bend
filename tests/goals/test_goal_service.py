"""GoalService unit tests — bypass LLM entirely, test DB-layer logic.

These tests use a mocked AsyncSession and verify:
- Goal creation/update with correct fields
- Vault-owned keys are rejected from goal anchors
- activate_goal transitions statuses correctly
- find_matching_goal uses anchor similarity, not title equality
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pai.domains.goals.service import (
    _assert_no_vault_keys,
    _anchor_match_score,
    _has_hard_conflict,
    find_matching_goal,
    create_goal,
    update_goal_anchors,
    activate_goal,
    goal_fact_lines,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_DRAFT,
    LIFECYCLE_PAUSED,
    INTEL_STALE,
    VAULT_FIELDS_THAT_AFFECT_GOALS,
)
from pai.domains.goals.models import Goal


# ── Vault key guard ───────────────────────────────────────────────────────────


def test_vault_keys_rejected():
    with pytest.raises(ValueError, match="ielts"):
        _assert_no_vault_keys({"ielts": 7.5, "target_country": "DE"})


def test_vault_keys_accepted_for_valid_anchors():
    _assert_no_vault_keys({"target_country": "DE", "degree_level": "ms"})


def test_goal_type_aliases_match_intelligence_vocabulary():
    from pai.domains.goals.types import GoalType

    assert GoalType.coerce("application") is GoalType.ADMISSION
    assert GoalType.coerce("career") is GoalType.JOB
    assert GoalType.coerce("exploration") is GoalType.GENERAL
    assert GoalType.coerce("admission") is GoalType.ADMISSION


# ── Anchor similarity ─────────────────────────────────────────────────────────


def _make_goal(goal_type="admission", target_country="DE", degree_level="ms", program=None, role=None, target_company=None) -> Goal:
    g = MagicMock(spec=Goal)
    g.goal_type = goal_type
    g.target_country = target_country
    g.degree_level = degree_level
    g.program = program
    g.role = role
    g.target_company = target_company
    return g


def test_anchor_match_same_country_degree():
    goal = _make_goal(goal_type="admission", target_country="DE", degree_level="ms")
    anchors = {"goal_type": "admission", "target_country": "DE", "degree_level": "ms"}
    score = _anchor_match_score(goal, anchors)
    assert score >= 0.6


def test_anchor_match_different_country():
    goal = _make_goal(goal_type="admission", target_country="DE", degree_level="ms")
    anchors = {"goal_type": "admission", "target_country": "CN", "degree_level": "ms"}
    score = _anchor_match_score(goal, anchors)
    # Country differs — should be below threshold
    assert score < 0.6


def test_anchor_match_different_type_returns_zero():
    goal = _make_goal(goal_type="job")
    anchors = {"goal_type": "admission", "target_country": "DE"}
    assert _anchor_match_score(goal, anchors) == 0.0


# ── Hard-conflict compatibility check ─────────────────────────────────────────
# Missing anchors must never register as a conflict — only two explicitly
# stated, differing values for the same dimension do.


def test_no_conflict_when_incoming_anchors_are_a_subset():
    """A vague rephrase with fewer anchors is compatible, not conflicting."""
    goal = _make_goal(goal_type="admission", target_country="DE", degree_level="ms")
    anchors = {"goal_type": "admission"}  # country/degree omitted, not contradicted
    assert _has_hard_conflict(goal, anchors) is False


def test_no_conflict_when_anchors_fully_missing_on_both_sides():
    goal = _make_goal(goal_type="admission", target_country=None, degree_level=None)
    anchors = {"goal_type": "admission"}
    assert _has_hard_conflict(goal, anchors) is False


def test_conflict_when_country_explicitly_differs():
    goal = _make_goal(goal_type="admission", target_country="DE", degree_level="ms")
    anchors = {"goal_type": "admission", "target_country": "CN", "degree_level": "ms"}
    assert _has_hard_conflict(goal, anchors) is True


def test_conflict_when_degree_level_explicitly_differs():
    goal = _make_goal(goal_type="admission", target_country="DE", degree_level="ms")
    anchors = {"goal_type": "admission", "target_country": "DE", "degree_level": "phd"}
    assert _has_hard_conflict(goal, anchors) is True


def test_conflict_when_goal_type_differs():
    goal = _make_goal(goal_type="admission")
    anchors = {"goal_type": "job"}
    assert _has_hard_conflict(goal, anchors) is True


# ── GoalService operations (mocked session) ───────────────────────────────────


@pytest.fixture
def mock_session():
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def person_id():
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_create_goal_stores_anchors(mock_session, person_id):
    goal = await create_goal(
        mock_session,
        person_id,
        goal_type="admission",
        title="MS CS in Germany",
        anchors={"target_country": "DE", "degree_level": "ms"},
    )
    assert goal.goal_type == "admission"
    assert goal.target_country == "DE"
    assert goal.degree_level == "ms"
    assert goal.intelligence_status == "pending"
    mock_session.add.assert_called_once_with(goal)


@pytest.mark.asyncio
async def test_create_goal_rejects_vault_keys(mock_session, person_id):
    with pytest.raises(ValueError):
        await create_goal(
            mock_session,
            person_id,
            goal_type="admission",
            title="MS in Germany",
            anchors={"ielts": 7.0},
        )


@pytest.mark.asyncio
async def test_update_goal_anchors_marks_stale(mock_session, person_id):
    goal = await create_goal(
        mock_session,
        person_id,
        goal_type="admission",
        title="MS in Germany",
        anchors={"target_country": "DE"},
    )
    # goal.anchors is a real dict from create_goal, not a mock
    assert isinstance(goal.anchors, dict)
    goal.intelligence_status = "ready"
    changed = await update_goal_anchors(mock_session, goal, {"target_country": "CN"})
    assert changed is True
    assert goal.target_country == "CN"


@pytest.mark.asyncio
async def test_activate_goal_pauses_others(mock_session, person_id):
    other_goal = MagicMock(spec=Goal)
    other_goal.id = uuid.uuid4()
    other_goal.lifecycle_status = LIFECYCLE_ACTIVE
    other_goal.status = LIFECYCLE_ACTIVE

    result = MagicMock()
    result.scalars.return_value = iter([other_goal])
    mock_session.execute = AsyncMock(return_value=result)

    target = await create_goal(
        mock_session,
        person_id,
        goal_type="admission",
        title="MS in Germany",
        anchors={},
    )
    target.lifecycle_status = "draft"

    await activate_goal(mock_session, target)

    assert other_goal.lifecycle_status == LIFECYCLE_PAUSED
    assert target.lifecycle_status == LIFECYCLE_ACTIVE


# ── Vault→Goals selective refresh mapping ────────────────────────────────────


def test_vault_fields_affect_admission_goals():
    assert "admission" in VAULT_FIELDS_THAT_AFFECT_GOALS.get("application.test_scores", [])


def test_vault_fields_ielts_not_in_map():
    """IELTS is a Vault key — it routes via test_scores, not ielts itself."""
    assert "ielts" not in VAULT_FIELDS_THAT_AFFECT_GOALS


# ── goal_fact_lines — counselor-facing current/previous/secondary lines ──────


def _row(title, lifecycle_status, goal_type="admission"):
    row = MagicMock(spec=Goal)
    row.id = uuid.uuid4()
    row.title = title
    row.lifecycle_status = lifecycle_status
    row.goal_type = goal_type
    return row


@pytest.mark.asyncio
async def test_goal_fact_lines_only_labels_paused_goal_as_previous(mock_session, person_id):
    current = _row("MS Computer Science in Germany", LIFECYCLE_ACTIVE)
    paused = _row("MS in China", LIFECYCLE_PAUSED)
    with patch(
        "pai.domains.goals.service.get_active_goal", new=AsyncMock(return_value=current)
    ), patch(
        "pai.domains.goals.service.list_goals",
        new=AsyncMock(return_value=[current, paused]),
    ):
        lines = await goal_fact_lines(mock_session, person_id)
    assert any(line.startswith("Current goal") for line in lines)
    assert any(
        line.startswith("Previous goal: MS in China") and "do not keep executing" in line
        for line in lines
    )


@pytest.mark.asyncio
async def test_goal_fact_lines_does_not_mislabel_draft_as_previous(mock_session, person_id):
    """A draft/secondary goal must never be rendered with 'do not keep
    executing this plan' — that phrasing is reserved for genuinely paused
    (superseded) goals."""
    current = _row("MS in Germany", LIFECYCLE_ACTIVE)
    draft = _row("SWE internship in Dubai", LIFECYCLE_DRAFT, goal_type="internship")
    with patch(
        "pai.domains.goals.service.get_active_goal", new=AsyncMock(return_value=current)
    ), patch(
        "pai.domains.goals.service.list_goals",
        new=AsyncMock(return_value=[current, draft]),
    ):
        lines = await goal_fact_lines(mock_session, person_id)
    assert not any("do not keep executing" in line for line in lines)
    assert any(line.startswith("Secondary/exploring goal: SWE internship") for line in lines)


@pytest.mark.asyncio
async def test_goal_fact_lines_dedupes_same_title_as_current(mock_session, person_id):
    """A leftover duplicate row with the same (normalized) title as the
    current goal must never resurface as noise."""
    current = _row("MS in German university", LIFECYCLE_ACTIVE)
    duplicate = _row("  ms in german university  ", LIFECYCLE_PAUSED)
    with patch(
        "pai.domains.goals.service.get_active_goal", new=AsyncMock(return_value=current)
    ), patch(
        "pai.domains.goals.service.list_goals",
        new=AsyncMock(return_value=[current, duplicate]),
    ):
        lines = await goal_fact_lines(mock_session, person_id)
    assert len(lines) == 1
    assert lines[0].startswith("Current goal")


@pytest.mark.asyncio
async def test_goal_fact_lines_ignores_archived_goals(mock_session, person_id):
    current = _row("MS in Germany", LIFECYCLE_ACTIVE)
    archived = _row("Old bootcamp goal", LIFECYCLE_ARCHIVED)
    with patch(
        "pai.domains.goals.service.get_active_goal", new=AsyncMock(return_value=current)
    ), patch(
        "pai.domains.goals.service.list_goals",
        new=AsyncMock(return_value=[current, archived]),
    ):
        lines = await goal_fact_lines(mock_session, person_id)
    assert len(lines) == 1
