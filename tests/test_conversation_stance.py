"""Conversation stance: deterministic counselor posture per turn (no LLM)."""

from __future__ import annotations

from pai.intelligences.counselor.conversation_stance import (
    ConversationStance,
    compute_stance,
)


def _phase(**kwargs) -> str:
    base = dict(
        message="",
        turn_kind="PERSONAL_ADVICE",
        is_greeting=False,
        has_active_goal=False,
    )
    base.update(kwargs)
    return compute_stance(**base).phase


def test_greeting_is_answer():
    assert _phase(message="hi", is_greeting=True) == "answer"


def test_factual_live_research_is_answer_even_with_goal():
    assert (
        _phase(
            message="What is the IELTS requirement for TUM?",
            turn_kind="LIVE_RESEARCH",
            has_active_goal=True,
            active_goal_status="ready",
            prior_assistant_turns=5,
        )
        == "answer"
    )


def test_uncertain_user_without_goal_explores():
    assert (
        _phase(message="I want to study abroad but I have no idea where")
        == "explore"
    )


def test_confident_new_direction_without_goal_is_understand():
    assert (
        _phase(message="I want to do an MS in CS in Germany. I've decided.")
        == "understand"
    )


def test_profile_update_without_goal_is_understand():
    assert _phase(message="I scored 7.5 on IELTS", turn_kind="PROFILE_UPDATE") == "understand"


def test_plain_question_without_goal_is_answer():
    assert (
        _phase(message="Can you tell me how counseling here works?")
        == "answer"
    )


def test_confident_user_with_new_goal_is_understand_not_guide():
    # Goal exists but intelligence not ready yet -> understand before executing.
    assert (
        _phase(
            message="Let's plan it",
            has_active_goal=True,
            active_goal_status="pending",
            prior_assistant_turns=1,
        )
        == "understand"
    )


def test_established_goal_with_intelligence_guides():
    assert (
        _phase(
            message="Okay what next for my applications",
            has_active_goal=True,
            active_goal_status="ready",
            prior_assistant_turns=4,
        )
        == "guide"
    )


def test_ready_goal_but_too_early_still_understands():
    # Intelligence ready but only the opening exists -> not yet guiding.
    assert (
        _phase(
            message="tell me more",
            has_active_goal=True,
            active_goal_status="ready",
            prior_assistant_turns=1,
        )
        == "understand"
    )


def test_new_uncertainty_about_existing_goal_pulls_back_to_understand():
    assert (
        _phase(
            message="Honestly I'm not sure Germany is right for me anymore",
            has_active_goal=True,
            active_goal_status="ready",
            prior_assistant_turns=6,
        )
        == "understand"
    )


def test_peer_pressure_with_goal_is_understand():
    assert (
        _phase(
            message="My parents want me to do an MBA",
            has_active_goal=True,
            active_goal_status="ready",
            prior_assistant_turns=4,
            decision_signal=True,
        )
        == "understand"
    )


def test_focus_text_is_populated_for_every_phase():
    cases = (
        dict(message="hi", turn_kind="PERSONAL_ADVICE", is_greeting=True, has_active_goal=False),
        dict(message="no idea where to go", turn_kind="PERSONAL_ADVICE", is_greeting=False, has_active_goal=False),
        dict(message="I want to study in Germany", turn_kind="PERSONAL_ADVICE", is_greeting=False, has_active_goal=False),
        dict(
            message="what next",
            turn_kind="PERSONAL_ADVICE",
            is_greeting=False,
            has_active_goal=True,
            active_goal_status="ready",
            prior_assistant_turns=3,
        ),
    )
    seen_phases = set()
    for kwargs in cases:
        stance = compute_stance(**kwargs)
        assert isinstance(stance, ConversationStance)
        assert stance.focus and len(stance.focus) > 10
        seen_phases.add(stance.phase)
    assert seen_phases == {"answer", "explore", "understand", "guide"}
