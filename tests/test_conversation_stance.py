"""Semantic interpretation owns posture; deterministic fallback never guesses."""
from unittest.mock import AsyncMock
import pytest
from pai.intelligences.counselor.conversation_stance import compute_stance
from pai.intelligences.understanding.schemas import TurnUnderstanding
from pai.intelligences.understanding.turn import understand_turn

@pytest.mark.parametrize("message", ["My father supports me", "I feel uncertain", "میرا ارادہ بدل گیا", "我想换专业"])
def test_fallback_does_not_infer_posture_from_words(message):
    result = compute_stance(message=message, turn_kind="PERSONAL_ADVICE",
        is_greeting=False, has_active_goal=True)
    assert result.phase == "answer"

@pytest.mark.asyncio
async def test_semantic_intervention_uses_original_language_and_context():
    gateway = AsyncMock()
    gateway.run.return_value = TurnUnderstanding(stance="explore", intervention="ask",
        focus="Clarify the student's own preference.", question="آپ کیا چاہتے ہیں؟",
        question_field="career.projects", evidence="میرا ارادہ بدل گیا")
    result = await understand_turn(gateway, message="میرا ارادہ بدل گیا", recent=[],
        profile="Existing goal", candidates=["career.projects"])
    assert result.stance == "explore"
    assert result.question_field == "career.projects"
    assert "میرا ارادہ بدل گیا" in gateway.run.call_args.kwargs["messages"][1].content

@pytest.mark.asyncio
async def test_ungrounded_understanding_fails_to_neutral():
    gateway = AsyncMock()
    gateway.run.return_value = TurnUnderstanding(evidence="invented")
    result = await understand_turn(gateway, message="hello", recent=[], profile="", candidates=[])
    assert result.status == "unavailable"
    assert result.question is None

@pytest.mark.asyncio
async def test_unavailable_model_does_not_invent_intervention():
    gateway = AsyncMock()
    gateway.run.side_effect = RuntimeError("unavailable")
    result = await understand_turn(gateway, message="help", recent=[], profile="", candidates=[])
    assert result.status == "unavailable"

@pytest.mark.asyncio
async def test_unrecognized_candidate_cannot_be_written_to_discovery_tracking():
    gateway = AsyncMock()
    gateway.run.return_value = TurnUnderstanding(intervention="ask", question="Why?",
        question_field="invented.field", evidence="help")
    result = await understand_turn(gateway, message="help", recent=[], profile="", candidates=[])
    assert result.question_field is None
