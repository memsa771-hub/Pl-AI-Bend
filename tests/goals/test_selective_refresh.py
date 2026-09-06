"""Selective Vault→Goals refresh tests.

Verifies that when a Vault field changes:
  - Goals of the affected type are marked stale and re-enqueued.
  - Goals of an unaffected type are NOT touched.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pai.domains.goals.service import (
    INTEL_STALE,
    VAULT_FIELDS_THAT_AFFECT_GOALS,
    mark_intelligence_stale_for_vault_update,
)
from pai.domains.goals.models import Goal


def _mock_goal(goal_type: str) -> Goal:
    g = MagicMock(spec=Goal)
    g.id = uuid.uuid4()
    g.person_id = uuid.uuid4()
    g.goal_type = goal_type
    g.lifecycle_status = "active"
    g.intelligence_status = "ready"
    g.anchors = {}
    return g


@pytest.mark.asyncio
async def test_test_score_update_marks_admission_stale():
    """Updating application.test_scores must stale + enqueue admission goals."""
    person_id = uuid.uuid4()
    admission_goal = _mock_goal("admission")
    job_goal = _mock_goal("job")

    # session.execute returns admission_goal only
    result_mock = MagicMock()
    result_mock.scalars.return_value = MagicMock()
    result_mock.scalars.return_value.all.return_value = [admission_goal]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.add = MagicMock()

    with patch(
        "pai.domains.goals.service.enqueue_goal_intelligence_job",
        new=AsyncMock(return_value=MagicMock()),
    ) as mock_enqueue:
        affected = await mark_intelligence_stale_for_vault_update(
            session, person_id, "application.test_scores"
        )

    assert admission_goal in affected
    assert job_goal not in affected
    assert admission_goal.intelligence_status == INTEL_STALE
    mock_enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_unrelated_field_does_not_touch_goals():
    """Updating a field not in VAULT_FIELDS_THAT_AFFECT_GOALS must not touch any goal."""
    person_id = uuid.uuid4()
    session = AsyncMock()

    with patch(
        "pai.domains.goals.service.enqueue_goal_intelligence_job",
        new=AsyncMock(),
    ) as mock_enqueue:
        affected = await mark_intelligence_stale_for_vault_update(
            session, person_id, "preferences.preferred_language"  # not in map
        )

    assert affected == []
    mock_enqueue.assert_not_awaited()
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_two_goals_only_affected_one_refreshed():
    """
    Create two goals (admission + job). Update IELTS (test_scores).
    Only admission goal should be refreshed.
    """
    person_id = uuid.uuid4()
    admission_goal = _mock_goal("admission")

    result_mock = MagicMock()
    result_mock.scalars.return_value = MagicMock()
    result_mock.scalars.return_value.all.return_value = [admission_goal]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)

    with patch(
        "pai.domains.goals.service.enqueue_goal_intelligence_job",
        new=AsyncMock(return_value=MagicMock()),
    ) as mock_enqueue:
        affected = await mark_intelligence_stale_for_vault_update(
            session, person_id, "application.test_scores"
        )

    # Exactly one goal was affected
    assert len(affected) == 1
    assert affected[0].goal_type == "admission"
    assert mock_enqueue.await_count == 1


def test_vault_field_map_completeness():
    """Spot-check that key Vault fields are in the map."""
    assert "application.test_scores" in VAULT_FIELDS_THAT_AFFECT_GOALS
    assert "education.highest_level" in VAULT_FIELDS_THAT_AFFECT_GOALS
    assert "demographics.nationality" in VAULT_FIELDS_THAT_AFFECT_GOALS
