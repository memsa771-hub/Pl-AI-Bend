from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from pai.intelligences.goals.resolver import grounded_life_aim
from pai.kernel.contracts.schemas import GoalExtract


def _extract(
    source: str,
    *,
    kind: str = "life_aim",
    intent: str | None = None,
    mode: str | None = "pursuing",
    pivot: bool = False,
    evidence: str | None = None,
) -> GoalExtract:
    label = intent if intent is not None else source
    span = evidence if evidence is not None else label
    return GoalExtract(
        kind=kind,  # type: ignore[arg-type]
        stated=kind == "life_aim",
        intent=label,
        mode=mode,  # type: ignore[arg-type]
        supersedes_previous=pivot,
        evidence_text=span,
    )


def test_english_life_aim_is_stored():
    src = "I want to study locally in FAST"
    hit = grounded_life_aim(src, _extract(src, intent="study locally in FAST"))
    assert hit is not None
    assert "study locally" in hit.intent.lower()
    assert "fast" in hit.intent.lower()
    assert hit.mode == "pursuing"


def test_roman_urdu_life_aim_is_stored():
    src = "mujhe Germany jana hai"
    hit = grounded_life_aim(src, _extract(src))
    assert hit is not None
    assert "germany" in hit.intent.lower()
    src2 = "main FAST mein parhna chahta hoon"
    hit2 = grounded_life_aim(src2, _extract(src2))
    assert hit2 is not None
    assert "fast" in hit2.intent.lower()


def test_mixed_aim_and_attach_keeps_only_the_aim():
    src = "mujhe Germany jana hai, transcript baad mein bhejta hoon"
    hit = grounded_life_aim(
        src,
        _extract(src, intent="mujhe Germany jana hai", evidence="mujhe Germany jana hai"),
    )
    assert hit is not None
    assert "germany" in hit.intent.lower()
    assert "bhejta" not in hit.intent.lower()


def test_turn_actions_are_not_goals():
    cases = (
        ("no i will attach this", "attach this"),
        ("yeh attach karunga", "yeh attach karunga"),
        ("yeh dhoond do", "yeh dhoond do"),
        ("I need to find this", "find this"),
        ("baad mein bhejta hoon", "baad mein bhejta hoon"),
        ("I'll send it later", "I'll send it later"),
    )
    for source, intent in cases:
        hit = grounded_life_aim(
            source,
            _extract(source, kind="turn_action", intent=intent, evidence=intent),
        )
        assert hit is None, source


def test_stated_true_without_life_aim_kind_is_ignored():
    src = "no i will attach this"
    lying = GoalExtract(
        kind="none",
        stated=True,
        intent="attach this",
        mode="pursuing",
        evidence_text=src,
    )
    assert grounded_life_aim(src, lying) is None
    assert grounded_life_aim(src, None) is None


def test_model_cannot_invent_a_goal_not_in_the_message():
    src = "yeh attach karunga"
    invented = _extract(
        src,
        intent="study in Germany",
        evidence="study in Germany",
    )
    assert grounded_life_aim(src, invented) is None


def test_questions_and_greetings_are_not_goals():
    for src in (
        "Hi",
        "salam",
        "What universities fit my GPA?",
        "kya universities suggest karoge?",
        "Suggest universities that are best matched for me",
        "COkay lock in USTC and STJU",
    ):
        assert grounded_life_aim(src, _extract(src, kind="none", intent=None, evidence="")) is None


def test_exploring_and_pivot_come_from_the_classifier():
    src = "I am considering an internship in Dubai"
    hit = grounded_life_aim(
        src,
        _extract(src, intent="an internship in Dubai", mode="exploring"),
    )
    assert hit is not None
    assert hit.mode == "exploring"
    later = "I want to study internationally not local"
    pivot = grounded_life_aim(
        later,
        _extract(later, intent="study internationally not local", pivot=True),
    )
    assert pivot is not None
    assert pivot.supersedes is True


@pytest.mark.asyncio
async def test_goal_pivot_keeps_history(postgres_ready):
    from pai.domains.goals.service import create_goal, get_active_goal, list_goals
    from pai.domains.journey.models import PersonEvent
    from pai.domains.journey.service import record_goal_event
    from pai.domains.student.person.models import Person
    from pai.domains.student.person.service import PersonBootstrapService
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.platform.security.auth.provider import ProviderUser

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)
    user = ProviderUser(
        id=f"journey-{uuid.uuid4()}",
        email="journey@example.com",
        email_verified=True,
        display_name=None,
        roles=["user"],
        created_at="2026-01-01T00:00:00Z",
    )
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        assert person is not None
        first = await create_goal(
            session,
            person.id,
            goal_type="admission",
            title="study locally in FAST",
            anchors={"goal_type": "admission"},
            lifecycle_status="active",
        )
        await record_goal_event(
            session, person.id, kind="goal.created", title=first.title, goal_id=first.id
        )
        first.lifecycle_status = "paused"
        later = await create_goal(
            session,
            person.id,
            goal_type="admission",
            title="study internationally not local",
            anchors={"goal_type": "admission", "target_country": "DE"},
            lifecycle_status="active",
        )
        await record_goal_event(
            session, person.id, kind="goal.changed", title=later.title, goal_id=later.id
        )
        await session.commit()
        current = await get_active_goal(session, person.id)
        assert current is not None
        assert "international" in current.title.lower()
        paused = [g for g in await list_goals(session, person.id, include_archived=True) if g.lifecycle_status == "paused"]
        assert paused and "locally" in paused[0].title.lower()
        result = await session.execute(
            select(PersonEvent).where(
                PersonEvent.person_id == person.id,
                PersonEvent.kind.in_(("goal.created", "goal.changed")),
            )
        )
        assert {row.kind for row in result.scalars().all()} == {"goal.created", "goal.changed"}
