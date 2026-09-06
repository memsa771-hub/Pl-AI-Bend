"""Regression cases for global interpretation and canonical write safety."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from pai.domains.student.normalization.grades import parse_grade
from pai.intelligences.documents.normalization.dates import parse_date
from pai.intelligences.documents.reconciliation.comparators import values_equivalent
from pai.intelligences.documents.identity.names import names_match
from pai.intelligences.documents.classification.classifier import classify_content, DocumentClassification
from pai.intelligences.documents.security.validation import sniff_mime
from pai.intelligences.goals.worker import research_is_fresh
from pai.intelligences.vault.ground import evidence_in_source
from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.policy.verifier import validate_candidate
from pai.domains.memory.formation import drafts_from_turn, apply_draft, memory_key_for

def candidate(**kwargs):
    return VaultCandidate(**{"field_key": "education.gpa", "value": 3.4,
        "confidence": .97, "source_reference": "m1", "evidence_text": "3.4",
        "attributed_to": "self", "temporal_status": "current", **kwargs})

@pytest.mark.parametrize("value", [3.4, 8.7, 85, 17.5, "3,4"])
def test_native_grade_has_no_inferred_scale(value):
    grade = parse_grade(value)
    assert grade["scale"] is None
    assert grade["requires_confirmation"]
    assert grade["original_text"] == str(value)

@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, {"value": 4, "scale": 0}])
def test_invalid_grades_never_become_truth(value):
    assert parse_grade(value) is None

def test_grade_preserves_qualification_identity():
    grade = parse_grade({"id": "q1", "institution": "学校", "gpa": 17, "scale": 20})
    assert grade["id"] == "q1"
    assert grade["institution"] == "学校"
    assert grade["value"] == 17

def test_cross_scale_ratio_is_not_equivalence():
    assert not values_equivalent("education.gpa", {"value": 3.2, "scale": 4},
        {"value": 8, "scale": 10})

def test_known_same_grading_system_can_reinforce():
    value = {"value": 17, "scale": 20, "grading_system": "institution-specific"}
    assert values_equivalent("education.gpa", value, dict(value))

@pytest.mark.parametrize("raw", ["03/04/2004", "2025-02-30", "2024-03", "2024"])
def test_ambiguous_or_partial_dates_are_not_completed(raw):
    assert parse_date(raw) is None

def test_unambiguous_date_can_be_parsed():
    assert parse_date("24/03/2004") == "2004-03-24"

@pytest.mark.parametrize("text", ["是", "हाँ", "نعم", "é"])
def test_short_original_language_evidence_is_valid(text):
    assert evidence_in_source(text, text)
    assert names_match(text, text) == "matched"

@pytest.mark.parametrize("changes", [
    {"attributed_to": "friend"}, {"assertion_status": "negated"},
    {"assertion_status": "hypothetical"}, {"temporal_status": "future"},
    {"temporal_status": "unknown"}])
def test_non_student_or_noncurrent_claims_cannot_cross_gate(changes):
    assert validate_candidate(candidate(**changes)) is None

def test_unknown_grade_scale_requires_confirmation_at_direct_gate():
    result = validate_candidate(candidate())
    assert result is not None and result.requires_confirmation

def test_pending_memory_cannot_supersede_accepted_truth():
    accepted = drafts_from_turn(accepted=[candidate(value={"value": 3.4, "scale": 4})])[0]
    pending = drafts_from_turn(pending=[candidate(value={"value": 3.7, "scale": 4})])[0]
    assert accepted.memory_key != pending.memory_key

def test_repeated_pending_evidence_cannot_become_accepted():
    record = None
    for index in range(6):
        draft = drafts_from_turn(pending=[candidate(source_reference=f"m{index}")])[0]
        _, record, _ = apply_draft(record, draft)
    assert record.status == "candidate"
    assert record.confidence == .97

def test_replaying_source_with_changed_model_wording_does_not_rewrite_memory():
    first = drafts_from_turn(accepted=[candidate(field_key="location.current_city", value="東京")])[0]
    _, record, _ = apply_draft(None, first)
    replay = drafts_from_turn(accepted=[candidate(field_key="location.current_city", value="大阪")])[0]
    action, unchanged, _ = apply_draft(record, replay)
    assert action == "noop"
    assert unchanged.content == record.content

def test_non_latin_observations_have_distinct_identities():
    first = candidate(field_key="memory.observed", value="医学を学びたい")
    second = candidate(field_key="memory.observed", value="音楽を学びたい")
    assert memory_key_for(first) != memory_key_for(second)

def test_repeatable_records_do_not_collapse_into_one_memory():
    first = candidate(field_key="education.records", value={"degree": "Abitur"})
    second = candidate(field_key="education.records", value={"degree": "Matura"})
    assert memory_key_for(first) != memory_key_for(second)

def test_binary_file_cannot_claim_text_by_extension():
    assert sniff_mime(b"\x00\xff\x00\x00", "resume.txt") is None

@pytest.mark.asyncio
async def test_classification_needs_content_evidence():
    gateway = SimpleNamespace(run=AsyncMock(return_value=DocumentClassification(
        document_type="transcript", evidence="invented")))
    with pytest.raises(ValueError, match="Ungrounded"):
        await classify_content(gateway, text="ordinary letter", source_type="document_vault")

def test_research_cache_does_not_refresh_its_own_age():
    now = datetime.now(UTC)
    assert research_is_fresh({"retrieved_at": (now-timedelta(hours=1)).isoformat()}, now=now)
    assert not research_is_fresh({"retrieved_at": (now-timedelta(days=2)).isoformat()}, now=now)
    assert not research_is_fresh({}, now=now)
    assert not research_is_fresh({"retrieved_at": (now+timedelta(days=1)).isoformat()}, now=now)

@pytest.mark.asyncio
async def test_pending_write_preserves_active_value_and_typed_records(postgres_ready):
    from sqlalchemy import select
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.platform.security.auth.provider import ProviderUser
    from pai.domains.student.person.service import PersonBootstrapService
    from pai.domains.student.person.models import Person, VaultValue, Education
    from pai.kernel.evidence.vault_apply import process_candidates
    reset_engine_for_tests()
    async with get_session_factory(postgres_ready)() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, ProviderUser(
            id=str(uuid.uuid4()), email="pending-regression@example.com", email_verified=True,
            roles=[], created_at="2026-01-01T00:00:00Z"))
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        await session.refresh(person, attribute_names=["vault"])
        stable = candidate(field_key="location.current_city", value="東京")
        await process_candidates(session, person, [stable])
        await session.commit()
        pending = stable.model_copy(update={"value": "大阪", "requires_confirmation": True})
        await process_candidates(session, person, [pending, candidate(requires_confirmation=True)])
        await session.commit()
        rows = (await session.execute(select(VaultValue).where(
            VaultValue.vault_id == person.vault.id))).scalars().all()
        assert any(r.status == "active" and r.value == "東京" for r in rows)
        assert any(r.status == "pending_confirmation" and r.value == "大阪" for r in rows)
        assert not (await session.execute(select(Education).where(
            Education.person_id == person.id))).scalars().all()


@pytest.mark.asyncio
async def test_stale_worker_cannot_pin_a_newer_attempt():
    from pai.platform.jobs.lease import pin_lease
    from pai.platform.jobs.models import PersonJob
    job = PersonJob(id=uuid.uuid4(), attempts=1, status="processing")
    current = SimpleNamespace(attempts=2, status="processing")
    result = MagicMock()
    result.scalar_one_or_none.return_value = current
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    assert not await pin_lease(session, job)


@pytest.mark.asyncio
async def test_exhausted_expired_lease_becomes_failed():
    from pai.platform.jobs.lease import reclaim_expired_leases
    from pai.platform.jobs.models import PersonJob
    job = PersonJob(attempts=3, status="processing")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    await reclaim_expired_leases(session, PersonJob)
    assert job.status == "failed"
    assert job.locked_at is None


@pytest.mark.asyncio
async def test_malformed_gap_stage_cannot_report_success():
    from pai.intelligences.goals.pipeline import run_gaps_stage
    gateway = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(content='{"gaps":[{"blocking":"maybe"}]}')))
    result = await run_gaps_stage(gateway, assessment={}, goal_type="general", goal_title="study")
    assert result == []
    assert result.failed
