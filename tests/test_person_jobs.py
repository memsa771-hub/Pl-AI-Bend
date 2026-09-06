from __future__ import annotations

import asyncio
import uuid

from pai.kernel.contracts.schemas import TaskProposal
from pai.platform.jobs.queue import (
    _CLAIM_SQL,
    claim_next_person_job,
    enqueue_intelligence,
    needs_intelligence,
)
from pai.domains.student.person.models import Person


def test_needs_intelligence_skips_greetings():
    assert needs_intelligence(extraction_required=False, task_proposals=[]) is False
    assert needs_intelligence(extraction_required=True, task_proposals=[]) is True
    assert needs_intelligence(
        extraction_required=False, task_proposals=[TaskProposal(title="Prep IELTS")]
    ) is True


def test_claim_sql_locks_the_student_not_just_the_job_row():
    assert "pg_try_advisory_xact_lock" in _CLAIM_SQL
    assert "hashtext(c.person_id::text)" in _CLAIM_SQL


def test_claim_serializes_one_student(postgres_ready):
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.domains.conversations.models import Conversation

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)

    async def _run():
        async with factory() as session:
            a = Person(
                auth_provider="supabase",
                external_auth_id=f"job-a-{uuid.uuid4()}",
                email=f"job-a-{uuid.uuid4()}@example.com",
            )
            b = Person(
                auth_provider="supabase",
                external_auth_id=f"job-b-{uuid.uuid4()}",
                email=f"job-b-{uuid.uuid4()}@example.com",
            )
            session.add_all([a, b])
            await session.flush()
            conv_a = Conversation(person_id=a.id, title="PAI")
            conv_b = Conversation(person_id=b.id, title="PAI")
            session.add_all([conv_a, conv_b])
            await session.flush()
            enqueue_intelligence(
                session,
                person_id=a.id,
                conversation_id=conv_a.id,
                user_message="I want Germany",
                user_message_id=str(uuid.uuid4()),
                extraction_required=True,
                task_proposals=[],
                run_id=None,
            )
            enqueue_intelligence(
                session,
                person_id=a.id,
                conversation_id=conv_a.id,
                user_message="Actually France",
                user_message_id=str(uuid.uuid4()),
                extraction_required=True,
                task_proposals=[],
                run_id=None,
            )
            enqueue_intelligence(
                session,
                person_id=b.id,
                conversation_id=conv_b.id,
                user_message="I live in Berlin",
                user_message_id=str(uuid.uuid4()),
                extraction_required=True,
                task_proposals=[],
                run_id=None,
            )
            await session.commit()
            first = await claim_next_person_job(session)
            assert first is not None
            assert first.payload["user_message"] == "I want Germany"
            second = await claim_next_person_job(session)
            assert second is not None
            assert second.person_id == b.id
            third = await claim_next_person_job(session)
            assert third is None

    asyncio.run(_run())


def test_two_connections_cannot_claim_same_student(postgres_ready):
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.domains.conversations.models import Conversation

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)

    async def _run():
        async with factory() as session:
            person = Person(
                auth_provider="supabase",
                external_auth_id=f"job-race-{uuid.uuid4()}",
                email=f"job-race-{uuid.uuid4()}@example.com",
            )
            session.add(person)
            await session.flush()
            conv = Conversation(person_id=person.id, title="PAI")
            session.add(conv)
            await session.flush()
            enqueue_intelligence(
                session,
                person_id=person.id,
                conversation_id=conv.id,
                user_message="I want Germany",
                user_message_id=str(uuid.uuid4()),
                extraction_required=True,
                task_proposals=[],
                run_id=None,
            )
            enqueue_intelligence(
                session,
                person_id=person.id,
                conversation_id=conv.id,
                user_message="Actually France",
                user_message_id=str(uuid.uuid4()),
                extraction_required=True,
                task_proposals=[],
                run_id=None,
            )
            await session.commit()

        async def _claim():
            async with factory() as session:
                return await claim_next_person_job(session)

        first, second = await asyncio.gather(_claim(), _claim())
        claimed = [job for job in (first, second) if job is not None]
        assert len(claimed) == 1
        assert claimed[0].payload["user_message"] == "I want Germany"

    asyncio.run(_run())
