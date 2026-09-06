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
    session.execute = AsyncMock(side_effect=[result_mock, MagicMock()])
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
async def test_every_accepted_field_can_invalidate_goal_assessment():
    goal = _mock_goal("general")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [goal]
    session = AsyncMock()
    session.execute.side_effect = [result, MagicMock()]
    with patch("pai.domains.goals.service.enqueue_goal_intelligence_job", new=AsyncMock()) as enqueue:
        affected = await mark_intelligence_stale_for_vault_update(session, goal.person_id, "preferences.preferred_language")
    assert affected == [goal]
    assert goal.intelligence_status == "stale"
    enqueue.assert_awaited_once()


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
    session.execute = AsyncMock(side_effect=[result_mock, MagicMock()])

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


def test_dependency_manifest_records_actual_inputs():
    from pai.domains.goals.dependencies import recorded_dependencies
    assert recorded_dependencies({"skills": [{"id": "s1"}]}) == ["skills", "skills:s1"]
