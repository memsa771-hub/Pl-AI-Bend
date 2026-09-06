"""Behavioral tests: extraction gate, typed profile context, education upsert, task filter."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from pai.domains.actions.service import is_fact_recording_task, process_task_proposals
from pai.domains.goals.models import Goal
from pai.domains.student.person.models import Education, Person
from pai.domains.student.vault.catalog import extraction_catalog_hint
from pai.intelligences.counselor.context import build_student_context_pack
from pai.intelligences.counselor.routing import should_extract_facts
from pai.kernel.contracts.schemas import TaskProposal, VaultCandidate
from pai.kernel.evidence.vault_apply import process_candidates


def test_pk_admissions_messages_trigger_extraction():
    assert should_extract_facts("PRE MEDICAL 877/1100") is True
    assert should_extract_facts("I completed FSc Pre-Medical") is True
    assert should_extract_facts("I live in Islamabad and want FAST or NUST") is True
    assert should_extract_facts("Additional Maths yes") is True
    assert should_extract_facts("BSCS from Pakistan") is True
    assert should_extract_facts("Hello") is False


def test_extraction_catalog_lists_admissions_keys():
    hint = extraction_catalog_hint()
    assert "education.program" in hint
    assert "education.marks" in hint
    assert "application.career_interest" in hint
    assert "application.target_universities" in hint
    assert "location.current_city" in hint
    assert "career.work_history" in hint
    assert "career.skills" in hint
    assert "FAST" not in hint
    assert "NUST" not in hint
    assert "GIKI" not in hint


def test_global_admissions_messages_trigger_extraction():
    assert should_extract_facts("I live in Dubai and want NYU Abu Dhabi") is True
    assert should_extract_facts("I am doing A-Levels in Sharjah") is True
    assert should_extract_facts("My EmSAT score is 1600") is True


def test_fact_recording_tasks_are_rejected():
    assert is_fact_recording_task("Record your FSc Pre-Medical education") is True
    assert is_fact_recording_task("Prepare for NUST NET") is False


def test_education_payload_keeps_marks_and_rejects_orphan_gpa_fabrication():
    from pai.domains.student.typed_apply import _education_payload

    payload = _education_payload(
        {
            "degree": "FSc",
            "major": "Pre-Medical",
            "marks_obtained": 877,
            "marks_total": 1100,
        }
    )
    assert payload is not None
    assert payload["degree"] == "FSc"
    assert payload["major"] == "Pre-Medical"
    assert payload["percentage"] == pytest.approx(79.73, abs=0.05)
    assert "institution" not in payload
    assert "Primary education" not in str(payload)
    as_degree = _education_payload("FSc Pre-Medical")
    assert as_degree == {"degree": "FSc Pre-Medical"}


@pytest.mark.asyncio
async def test_education_marks_upsert_and_full_context(postgres_ready):
    from pai.domains.student.person.service import PersonBootstrapService
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.platform.security.auth.provider import ProviderUser

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)
    user = ProviderUser(
        id=f"flow-{uuid.uuid4()}",
        email="flow@example.com",
        email_verified=True,
        display_name=None,
        roles=["user"],
        created_at="2026-01-01T00:00:00Z",
    )
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        assert person is not None
        await session.refresh(person, attribute_names=["vault"])

        orphan = VaultCandidate(
            field_key="education.program",
            value={
                "degree": "FSc",
                "major": "Pre-Medical",
                "marks_obtained": 847,
                "marks_total": 1100,
            },
            confidence=0.95,
            evidence_text="I completed FSc Pre-Medical 847/1100",
            source_reference=str(uuid.uuid4()),
            source_type="chat",
            explicitness="explicit",
        )
        orphan_out, _ = await process_candidates(session, person, [orphan])
        await session.commit()
        assert orphan_out == []
        edu_q = select(Education).where(Education.person_id == person.id)
        assert list((await session.execute(edu_q)).scalars()) == []

        first = VaultCandidate(
            field_key="education.program",
            value={
                "institution": "Punjab College",
                "degree": "FSc",
                "major": "Pre-Medical",
                "marks_obtained": 847,
                "marks_total": 1100,
            },
            confidence=0.95,
            evidence_text="I completed FSc Pre-Medical 847/1100",
            source_reference=str(uuid.uuid4()),
            source_type="chat",
            explicitness="explicit",
        )
        outcomes, _ = await process_candidates(session, person, [first])
        await session.commit()
        assert outcomes
        assert outcomes[0].status in ("accepted", "updated")

        correction = VaultCandidate(
            field_key="education.program",
            value={
                "degree": "FSc",
                "major": "Pre-Medical",
                "marks_obtained": 877,
                "marks_total": 1100,
            },
            confidence=0.97,
            evidence_text="Correction: PRE MEDICAL 877/1100",
            source_reference=str(uuid.uuid4()),
            source_type="chat",
            explicitness="explicit",
            is_correction=True,
        )
        await process_candidates(session, person, [correction])
        await session.commit()

        rows = list(
            (
                await session.execute(
                    select(Education).where(Education.person_id == person.id)
                )
            ).scalars()
        )
        assert len(rows) == 1
        assert rows[0].institution == "Punjab College"
        assert rows[0].institution != rows[0].degree
        assert rows[0].major == "Pre-Medical"
        assert rows[0].percentage == pytest.approx(79.73, abs=0.05)

        goal = VaultCandidate(
            field_key="application.career_interest",
            value="BSCS in Pakistan",
            confidence=0.9,
            evidence_text="I want BSCS in Pakistan",
            source_reference=str(uuid.uuid4()),
            source_type="chat",
            explicitness="explicit",
        )
        await process_candidates(session, person, [goal])
        await process_candidates(
            session,
            person,
            [
                VaultCandidate(
                    field_key="application.career_interest",
                    value="BSCS",
                    confidence=0.9,
                    evidence_text="BSCS",
                    source_reference=str(uuid.uuid4()),
                    source_type="chat",
                    explicitness="explicit",
                )
            ],
        )
        await session.commit()
        goals = list(
            (await session.execute(select(Goal).where(Goal.person_id == person.id))).scalars()
        )
        assert len(goals) == 1

        pack = await build_student_context_pack(session, person, settings=postgres_ready)
        typed = pack.typed_profile_summary
        assert typed["educations"]
        assert typed["educations"][0]["major"] == "Pre-Medical"
        assert typed["educations"][0]["percentage"] == pytest.approx(79.73, abs=0.05)
        assert typed["goals"]
        assert "BSCS" in typed["goals"][0]["title"]

        rejected = await process_task_proposals(
            session,
            person,
            [TaskProposal(title="Record your FSc Pre-Medical education")],
        )
        assert rejected[0].status == "rejected"
        ok = await process_task_proposals(
            session,
            person,
            [TaskProposal(title="Prepare for NUST NET")],
        )
        assert ok[0].status == "proposed"


@pytest.mark.asyncio
async def test_no_fabricated_primary_education_for_bare_gpa(postgres_ready):
    from pai.domains.student.person.service import PersonBootstrapService
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.platform.security.auth.provider import ProviderUser

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)
    user = ProviderUser(
        id=f"bare-{uuid.uuid4()}",
        email="bare@example.com",
        email_verified=True,
        display_name=None,
        roles=["user"],
        created_at="2026-01-01T00:00:00Z",
    )
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        assert person is not None
        await session.refresh(person, attribute_names=["vault"])
        candidate = VaultCandidate(
            field_key="education.gpa",
            value=3.4,
            confidence=0.9,
            evidence_text="my gpa is 3.4",
            source_reference=str(uuid.uuid4()),
            source_type="chat",
            explicitness="explicit",
        )
        outcomes, _ = await process_candidates(session, person, [candidate])
        await session.commit()
        # Rejected — no invented "Primary education" row
        assert not outcomes or outcomes[0].status == "rejected"
        rows = list(
            (
                await session.execute(
                    select(Education).where(Education.person_id == person.id)
                )
            ).scalars()
        )
        assert rows == []
