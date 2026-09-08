"""Longitudinal student world model: canonical levels, education entity
resolution, timeline validation, evidence-weighted contradictions, and test
attempts as repeatable entities."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import select

from pai.domains.student.education.levels import (
    canonical_level,
    classify_qualification,
    levels_between,
)
from pai.domains.student.education.resolver import resolve_education
from pai.domains.student.education.timeline import (
    order_timeline,
    validate_education_timeline,
)
from pai.domains.student.person.models import Education, Person, ProfileIssue
from pai.domains.student.person.models import TestAttempt as TestAttemptRow
from pai.domains.student.test_attempts import (
    attempt_status,
    normalize_test_type,
    parse_test_observation,
    validity_until,
)
from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.evidence.vault_apply import process_candidates


# --------------------------------------------------------------------------
# Canonical levels
# --------------------------------------------------------------------------


def test_qualification_names_do_not_imply_canonical_levels():
    assert canonical_level("FSc Pre-Engineering") is None
    assert canonical_level("A-Levels") is None
    assert canonical_level("BSCS") is None
    assert canonical_level("higher_secondary") == "higher_secondary"


def test_explicit_canonical_level_has_no_invented_framework_or_country():
    level = classify_qualification("higher_secondary")
    assert level is not None
    assert (level.canonical_level, level.framework, level.country) == (
        "higher_secondary", None, None
    )


def test_levels_between_reports_skipped_stages():
    assert levels_between("secondary", "bachelor") == ["higher_secondary"]
    assert levels_between("secondary", "higher_secondary") == []
    assert levels_between("bachelor", "phd") == ["master"]


# --------------------------------------------------------------------------
# Entity resolution
# --------------------------------------------------------------------------


@dataclass
class _Row:
    """Stand-in for an Education row for pure resolver/timeline tests."""

    id: uuid.UUID
    institution: str
    degree: str | None = None
    major: str | None = None
    canonical_level: str | None = None
    graduation_year: int | None = None
    original_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None


def _timeline_rows() -> list[_Row]:
    return [
        _Row(uuid.uuid4(), "City School", "Matric", canonical_level="secondary",
             start_date=date(2018, 8, 1), end_date=date(2020, 6, 1), graduation_year=2020),
        _Row(uuid.uuid4(), "Punjab College", "FSc Pre-Engineering",
             canonical_level="higher_secondary", start_date=date(2020, 8, 1),
             end_date=date(2022, 6, 1), graduation_year=2022),
        _Row(uuid.uuid4(), "Bahria University", "BSCS", canonical_level="bachelor",
             start_date=date(2022, 9, 1), graduation_year=2026),
    ]


def test_resolver_attaches_grade_to_the_named_qualification():
    rows = _timeline_rows()
    outcome = resolve_education(
        {"degree": "FSc", "canonical_level": "higher_secondary", "percentage": 82.0}, rows
    )
    assert outcome.match is rows[1]
    assert outcome.ambiguous is False


def test_resolver_refuses_to_guess_when_grade_names_no_qualification():
    # "I got 82%" with three records on file: picking one would be a guess.
    outcome = resolve_education({"percentage": 82.0}, _timeline_rows())
    assert outcome.match is None
    assert outcome.ambiguous is True


def test_resolver_uses_the_only_record_when_there_is_no_ambiguity():
    rows = _timeline_rows()[:1]
    outcome = resolve_education({"gpa": 3.4}, rows)
    assert outcome.match is rows[0]
    assert outcome.ambiguous is False


def test_resolver_never_merges_different_levels():
    rows = _timeline_rows()
    # A bachelor's observation must not land on the FSc row even at the same college.
    outcome = resolve_education(
        {"degree": "BSCS", "canonical_level": "bachelor", "institution": "Punjab College"},
        [rows[1]],
    )
    assert outcome.match is None
    assert outcome.ambiguous is False


def test_resolver_matches_same_qualification_from_a_second_source():
    """The doc §29 case: a transcript must update the chat-created record."""
    rows = _timeline_rows()
    outcome = resolve_education(
        {
            "institution": "Punjab College",
            "degree": "FSc Pre-Engineering",
            "graduation_year": 2022,
            "percentage": 82.2,
        },
        rows,
    )
    assert outcome.match is rows[1]


# --------------------------------------------------------------------------
# Timeline validation
# --------------------------------------------------------------------------


def test_timeline_orders_records_chronologically():
    rows = _timeline_rows()
    shuffled = [rows[2], rows[0], rows[1]]
    assert [r.degree for r in order_timeline(shuffled)] == ["Matric", "FSc Pre-Engineering", "BSCS"]


def test_missing_stage_between_matric_and_bachelor_is_detected():
    rows = _timeline_rows()
    issues = validate_education_timeline([rows[0], rows[2]])
    missing = [i for i in issues if i.issue_type == "missing_stage"]
    assert len(missing) == 1
    assert missing[0].detail["missingLevel"] == "higher_secondary"
    assert missing[0].clarification_needed is True


def test_complete_ladder_reports_no_missing_stage():
    issues = validate_education_timeline(_timeline_rows())
    assert not [i for i in issues if i.issue_type == "missing_stage"]


def test_duplicate_records_of_the_same_qualification_are_detected():
    rows = _timeline_rows()
    dupe = _Row(
        uuid.uuid4(), "Punjab College Lahore", "FSc", canonical_level="higher_secondary"
    )
    issues = validate_education_timeline([rows[1], dupe])
    assert any(i.issue_type == "duplicate_entity" for i in issues)


def test_record_without_any_dates_is_flagged():
    row = _Row(uuid.uuid4(), "Some College", "FSc", canonical_level="higher_secondary")
    issues = validate_education_timeline([row])
    assert any(i.issue_type == "missing_dates" for i in issues)


def test_long_break_between_qualifications_is_flagged_but_low_severity():
    rows = [
        _Row(uuid.uuid4(), "A", "Matric", canonical_level="secondary",
             end_date=date(2016, 6, 1), graduation_year=2016),
        _Row(uuid.uuid4(), "B", "A-Levels", canonical_level="higher_secondary",
             start_date=date(2020, 8, 1), graduation_year=2022),
    ]
    gaps = [i for i in validate_education_timeline(rows) if i.issue_type == "date_gap"]
    assert len(gaps) == 1
    # A gap is not an error — it must not nag (doc §11).
    assert gaps[0].severity == "low"
    assert gaps[0].clarification_needed is False


# --------------------------------------------------------------------------
# Standardized tests
# --------------------------------------------------------------------------


def test_test_name_normalization():
    assert normalize_test_type("IELTS Academic") == "ielts"
    assert normalize_test_type("TOEFL iBT") == "toefl"
    assert normalize_test_type("GRE General") == "gre"
    assert normalize_test_type("Duolingo English Test") == "duolingo"
    assert normalize_test_type(None) is None


def test_parse_test_observation_from_string_and_dict():
    assert parse_test_observation("IELTS 7.5")["overall_numeric"] == 7.5
    parsed = parse_test_observation(
        {"name": "ielts", "score": "6.5", "date": "2024-03-01",
         "sections": {"Listening": 7, "Speaking": 6}}
    )
    assert parsed["test_date"] == date(2024, 3, 1)
    assert parsed["sections"] == {"listening": 7.0, "speaking": 6.0}


def test_language_test_scores_expire():
    expires = validity_until("ielts", date(2024, 3, 1))
    assert expires == date(2026, 3, 1)
    assert attempt_status(expires, today=date(2026, 9, 1)) == "expired"
    assert attempt_status(expires, today=date(2025, 9, 1)) == "valid"
    # No general expiry rule is invented for national entrance exams.
    assert validity_until("mdcat", date(2024, 3, 1)) is None


# --------------------------------------------------------------------------
# Discovery ranking
# --------------------------------------------------------------------------


@dataclass
class _Issue:
    id: uuid.UUID
    domain: str
    issue_type: str
    severity: str
    clarification_needed: bool
    clarification_prompt: str


def test_contradiction_outranks_routine_missing_fields():
    from pai.intelligences.counselor.discovery import select_discovery_candidates

    issue = _Issue(
        uuid.uuid4(), "education", "contradicting_grade", "high", True,
        "their FSc percentage is disputed",
    )
    result = select_discovery_candidates(
        missing_important=["preferences.learning_style"],
        issues=[issue],
        message="what are my chances for MS AI?",
    )
    assert result.top is not None
    assert result.top.kind == "issue"
    assert result.top.issue_id == str(issue.id)


def test_issue_that_cannot_be_asked_about_is_deprioritized():
    from pai.intelligences.counselor.discovery import score_issue

    askable = _Issue(uuid.uuid4(), "education", "missing_stage", "medium", True, "ask")
    silent = _Issue(uuid.uuid4(), "education", "missing_stage", "medium", False, "note")
    assert (
        score_issue(askable, message="", goal_type=None).score
        > score_issue(silent, message="", goal_type=None).score
    )


# --------------------------------------------------------------------------
# End-to-end persistence (doc §33)
# --------------------------------------------------------------------------


async def _bootstrap(postgres_ready, slug: str):
    from pai.platform.database.db import get_session_factory, reset_engine_for_tests
    from pai.platform.security.auth.provider import ProviderUser

    reset_engine_for_tests()
    factory = get_session_factory(postgres_ready)
    user = ProviderUser(
        id=f"{slug}-{uuid.uuid4()}",
        email=f"{slug}@example.com",
        email_verified=True,
        display_name=None,
        roles=["user"],
        created_at="2026-01-01T00:00:00Z",
    )
    return factory, user


def _edu_candidate(value, **kw) -> VaultCandidate:
    return VaultCandidate(
        field_key=kw.pop("field_key", "education.program"),
        value=value,
        confidence=kw.pop("confidence", 0.93),
        evidence_text=kw.pop("evidence_text", "stated by the student"),
        source_reference=str(uuid.uuid4()),
        **kw,
    )


@pytest.mark.asyncio
async def test_transcript_updates_the_chat_record_instead_of_duplicating(postgres_ready):
    """Doc §29: two sources describing one qualification must not become two rows."""
    from pai.domains.student.person.service import PersonBootstrapService

    factory, user = await _bootstrap(postgres_ready, "dedupe")
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        await session.refresh(person, attribute_names=["vault"])

        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    {
                        "institution": "Punjab College",
                        "degree": "FSc Pre-Engineering",
                        "graduation_year": 2022,
                        "percentage": 82,
                    }
                )
            ],
        )
        await session.commit()

        # Later, the transcript arrives with a slightly more precise figure.
        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    {
                        "institution": "Punjab College",
                        "degree": "FSc Pre-Engineering",
                        "graduation_year": 2022,
                        "percentage": 82.2,
                    },
                    source_type="document",
                    confidence=0.97,
                )
            ],
            from_document=True,
        )
        await session.commit()

        rows = list(
            (
                await session.execute(
                    select(Education).where(Education.person_id == person.id)
                )
            ).scalars()
        )
        assert len(rows) == 1
        assert rows[0].percentage == 82.2
        assert rows[0].canonical_level is None
        assert rows[0].framework is None


@pytest.mark.asyncio
async def test_weaker_claim_does_not_overwrite_verified_value(postgres_ready):
    """Doc §13: a casual restatement loses to a verified figure and is recorded."""
    from pai.domains.student.person.service import PersonBootstrapService

    factory, user = await _bootstrap(postgres_ready, "contradiction")
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        await session.refresh(person, attribute_names=["vault"])

        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    {
                        "institution": "Punjab College",
                        "degree": "FSc Pre-Engineering",
                        "percentage": 82.2,
                    },
                    source_type="document",
                    confidence=0.97,
                )
            ],
            from_document=True,
        )
        await session.commit()

        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    {
                        "institution": "Punjab College",
                        "degree": "FSc Pre-Engineering",
                        "percentage": 70,
                    },
                    confidence=0.9,
                )
            ],
        )
        await session.commit()

        row = (
            await session.execute(select(Education).where(Education.person_id == person.id))
        ).scalar_one()
        assert row.percentage == 82.2

        issues = list(
            (
                await session.execute(
                    select(ProfileIssue).where(
                        ProfileIssue.person_id == person.id,
                        ProfileIssue.issue_type == "contradicting_grade",
                    )
                )
            ).scalars()
        )
        assert len(issues) == 1
        assert issues[0].detail["claimed"] == 70
        assert issues[0].status == "open"


@pytest.mark.asyncio
async def test_missing_stage_is_recorded_and_clears_when_filled(postgres_ready):
    """Doc §12: PAI flags the hole, never invents the qualification."""
    from pai.domains.student.person.service import PersonBootstrapService

    factory, user = await _bootstrap(postgres_ready, "stage")
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        await session.refresh(person, attribute_names=["vault"])

        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    {"institution": "City School", "degree": "Matric", "graduation_year": 2020, "canonical_level": "secondary", "mapping_source": "test:verified-mapping"}
                ),
                _edu_candidate(
                    {"institution": "Bahria University", "degree": "BSCS", "graduation_year": 2026, "canonical_level": "bachelor", "mapping_source": "test:verified-mapping"}
                ),
            ],
        )
        await session.commit()

        open_issue = (
            await session.execute(
                select(ProfileIssue).where(
                    ProfileIssue.person_id == person.id,
                    ProfileIssue.issue_type == "missing_stage",
                    ProfileIssue.status == "open",
                )
            )
        ).scalar_one()
        assert open_issue.detail["missingLevel"] == "higher_secondary"
        # Only three education rows would exist if PAI had invented the stage.
        assert len(
            list(
                (
                    await session.execute(
                        select(Education).where(Education.person_id == person.id)
                    )
                ).scalars()
            )
        ) == 2

        # The student later supplies it; the issue closes on its own.
        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    {
                        "institution": "Punjab College",
                        "degree": "FSc Pre-Engineering",
                        "graduation_year": 2022,
                        "canonical_level": "higher_secondary",
                        "mapping_source": "test:verified-mapping",
                    }
                )
            ],
        )
        await session.commit()
        await session.refresh(open_issue)
        assert open_issue.status == "resolved"


@pytest.mark.asyncio
async def test_test_scores_completion_follows_the_typed_rows(postgres_ready):
    """`application.test_scores` moved storage, so completion must read attempts."""
    from pai.domains.student.person.service import PersonBootstrapService
    from pai.domains.student.vault.completion import (
        field_is_present_in_snapshot,
        load_presence_snapshot,
    )
    from pai.domains.student.vault.catalog import VAULT_CATALOG

    factory, user = await _bootstrap(postgres_ready, "completion")
    field = VAULT_CATALOG["application.test_scores"]
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        await session.refresh(person, attribute_names=["vault"])

        snapshot = await load_presence_snapshot(session, person, person.vault)
        assert field_is_present_in_snapshot(person, field, snapshot) is False

        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    [{"name": "IELTS", "score": "7.0", "date": "2026-01-10"}],
                    field_key="application.test_scores",
                )
            ],
        )
        await session.commit()

        snapshot = await load_presence_snapshot(session, person, person.vault)
        assert field_is_present_in_snapshot(person, field, snapshot) is True


@pytest.mark.asyncio
async def test_test_retake_is_a_new_attempt_not_an_overwrite(postgres_ready):
    """Doc §20: attempt 1 survives attempt 2."""
    from pai.domains.student.person.service import PersonBootstrapService

    factory, user = await _bootstrap(postgres_ready, "tests")
    async with factory() as session:
        boot = await PersonBootstrapService(postgres_ready).bootstrap(session, user)
        person = await session.get(Person, uuid.UUID(boot["person"]["id"]))
        await session.refresh(person, attribute_names=["vault"])

        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    [{"name": "IELTS", "score": "6.5", "date": "2024-02-01"}],
                    field_key="application.test_scores",
                )
            ],
        )
        await process_candidates(
            session,
            person,
            [
                _edu_candidate(
                    [{"name": "IELTS", "score": "7.5", "date": "2026-02-01"}],
                    field_key="application.test_scores",
                )
            ],
        )
        await session.commit()

        attempts = list(
            (
                await session.execute(
                    select(TestAttemptRow)
                    .where(TestAttemptRow.person_id == person.id)
                    .order_by(TestAttemptRow.attempt_number)
                )
            ).scalars()
        )
        assert [a.overall_numeric for a in attempts] == [6.5, 7.5]
        assert attempts[0].validity_until == date(2026, 2, 1)
        assert attempts[1].status == "valid"
