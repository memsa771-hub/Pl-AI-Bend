"""One counselor thread per person."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from pai.domains.conversations.service import get_or_create_person_conversation
from pai.intelligences.counselor.context import build_known_facts


def test_known_facts_lists_education_and_country():
    facts = build_known_facts(
        identity={"fullName": "Musawir"},
        sparse={
            "application.study_country": {"value": "Germany, China"},
            "finance.funding_status": {"value": "limited budget"},
            "preferences.learning_style": {"value": "project-based"},
        },
        typed={
            "educations": [
                {
                    "degree": "BCSS",
                    "institution": "Bahria",
                    "gpa": 3.35,
                    "gpaScale": 4.0,
                }
            ],
            "goals": [{"title": "MS in CS, AI, or Cyber"}],
            "skills": [{"name": "Python"}],
        },
    )
    joined = " | ".join(facts)
    assert "Musawir" in joined
    assert "3.35" in joined
    assert "Germany" in joined
    assert "MS in CS" in joined
    assert "limited budget" in joined
    assert "project-based" in joined
    assert "Python" in joined


def test_opening_uses_vault_facts_not_country_lists():
    from pai.intelligences.counselor.context import compose_opening

    text = compose_opening(
        {
            "identity": {"preferredName": "Sara", "fullName": "Sara Khan"},
            "known_facts": [
                "Student name: Sara",
                "Education: BSCS / Bahria, GPA/CGPA 3.4/4.0",
                "Target study country/countries: DE",
                "Career/study goal: MS Computer Science in Germany",
            ],
        }
    )
    assert "Sara" in text
    assert "PAI" in text
    assert "BSCS" in text
    assert "DE" in text
    assert "FAST" not in text
    assert "NUST" not in text


def test_opening_keeps_facts_without_duplicate_goal():
    from pai.intelligences.counselor.context import compose_opening

    text = compose_opening(
        {
            "identity": {"preferredName": "Musawir"},
            "known_facts": [
                "Student name: Musawir",
                "Current goal (pursuing): MS Computer Science, MS AI in Germany or CHINA",
                "Education: BSCS / computer_science / Bahria University, GPA/CGPA 3.35/4.0",
                "Career/study goal: MS Computer Science, MS AI in Germany or CHINA",
                "Skill: Python",
                "Target study country/countries: CN",
                "Test scores: [{'name': 'ielts', 'score': '7.5'}]",
                "Admission cycle: fall 2027",
            ],
        }
    )
    assert "Musawir" in text
    assert "3.35" in text
    assert "CN" in text
    assert "7.5" in text
    assert text.lower().count("career/study goal") == 0
    assert "FAST" not in text


def test_opening_without_profile_still_introduces_pai():
    from pai.intelligences.counselor.context import compose_opening

    text = compose_opening({"identity": {}, "known_facts": []})
    assert "PAI" in text
    assert "working toward" in text


def test_chat_starters_use_study_country():
    from pai.intelligences.counselor.context import build_chat_starters, chat_stay_payload

    pack = {
        "known_facts": [
            "Student name: Khan",
            "Target study country/countries: DE",
            "Education: BSCS / Bahria",
        ],
        "missing_critical_fields": ["location.current_city"],
        "typed_profile_summary": {"skills": [{"name": "Python"}]},
        "active_tasks": [],
    }
    starters = build_chat_starters(pack)
    assert any("DE" in item["message"] for item in starters)
    stay = chat_stay_payload(pack)
    assert stay["nextQuestion"] == "Which city are you in now?"
    assert len(stay["starters"]) == 3


@pytest.mark.asyncio
async def test_person_always_gets_the_same_conversation(postgres_ready):
    from pai.platform.security.auth.provider import ProviderUser
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.domains.student.person.models import Person
    from pai.domains.student.person.service import PersonBootstrapService

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)
    user = ProviderUser(
        id=f"cont-{uuid.uuid4()}",
        email="cont@example.com",
        email_verified=True,
        display_name=None,
        roles=["user"],
        created_at="2026-01-01T00:00:00Z",
    )
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        assert person is not None
        first = await get_or_create_person_conversation(session, person)
        second = await get_or_create_person_conversation(session, person)
        third = await get_or_create_person_conversation(session, person)
        assert second.id == first.id
        assert third.id == first.id
