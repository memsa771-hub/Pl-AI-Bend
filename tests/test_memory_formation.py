"""Memory formation: strengthen on repeat, version on change, don't dump blobs."""

from __future__ import annotations

from datetime import UTC, datetime

from pai.kernel.contracts.schemas import VaultCandidate
from pai.domains.memory.formation import (
    apply_draft,
    drafts_from_turn,
    format_for_recall,
    importance_of,
    memory_key_for,
    rank_score,
)


def _cand(**kwargs) -> VaultCandidate:
    base = dict(
        confidence=0.92,
        source_reference="m1",
        evidence_text="I want Germany",
        assertion_status="explicit",
    )
    base.update(kwargs)
    return VaultCandidate(**base)


def test_repeat_strengthens_same_memory():
    first = _cand(field_key="application.study_country", value="DE")
    drafts = drafts_from_turn(accepted=[first])
    assert len(drafts) == 1
    assert drafts[0].memory_key == "semantic:application.study_country"
    assert drafts[0].belongs_to == "goal:now"

    action, rec, old = apply_draft(None, drafts[0])
    assert action == "insert"
    assert rec.recurrence == 1
    assert old is None

    action, rec, old = apply_draft(rec, drafts[0])
    assert action == "strengthen"
    assert rec.recurrence == 2
    assert rec.stability > 0.2
    assert rec.evidence_count == 2
    assert old is None

    action, rec, _ = apply_draft(rec, drafts[0])
    action, rec, _ = apply_draft(rec, drafts[0])
    assert rec.recurrence == 4
    assert rec.status == "active"


def test_value_change_versions_instead_of_overwrite():
    germany = drafts_from_turn(
        accepted=[_cand(field_key="application.study_country", value="DE")]
    )[0]
    _, current, _ = apply_draft(None, germany)
    usa = drafts_from_turn(
        accepted=[
            _cand(
                field_key="application.study_country",
                value="US",
                is_correction=True,
                evidence_text="Germany is definitely not first, USA is",
            )
        ]
    )[0]
    action, new, old = apply_draft(current, usa)
    assert action == "supersede"
    assert old is not None
    assert old.status == "superseded"
    assert old.valid_until is not None
    assert new.version == 2
    assert new.previous_content == current.content
    assert "US" in new.content
    line = format_for_recall(new)
    assert "previously:" in line


def test_observed_negation_is_not_vault_semantic_key():
    negated = _cand(
        field_key="application.study_country",
        value="US",
        assertion_status="negated",
        evidence_text="I don't want the US",
    )
    drafts = drafts_from_turn(observed=[negated])
    assert drafts
    assert drafts[0].memory_key.startswith("observed:")
    assert "semantic:application.study_country" not in {d.memory_key for d in drafts}


def test_hypothetical_stays_candidate():
    maybe = _cand(
        field_key="application.study_country",
        value="US",
        assertion_status="hypothetical",
        evidence_text="If I get a scholarship, maybe the US",
    )
    drafts = drafts_from_turn(observed=[maybe])
    assert drafts[0].status == "candidate"
    assert importance_of(maybe) < 0.5


def test_conflict_does_not_share_live_semantic_key():
    conflict = _cand(field_key="application.study_country", value="US")
    drafts = drafts_from_turn(conflicts=[conflict])
    assert drafts[0].memory_key.startswith("claim:")
    assert drafts[0].status == "candidate"


def test_turn_links_related_memories():
    drafts = drafts_from_turn(
        accepted=[
            _cand(field_key="application.study_country", value="DE"),
            _cand(field_key="education.stream", value="A-Levels", evidence_text="A-Levels"),
        ]
    )
    keys = {d.memory_key for d in drafts}
    assert len(keys) == 2
    for row in drafts:
        assert row.related
        assert row.memory_key not in row.related


def test_rank_prefers_query_match_and_skips_unrelated():
    now = datetime.now(UTC)
    germany = drafts_from_turn(
        accepted=[_cand(field_key="application.study_country", value="DE")]
    )[0]
    _, rec, _ = apply_draft(None, germany, now=now)
    assert rank_score("I want Germany", rec, now=now) > 0
    assert rank_score("what is a banana", rec, now=now) <= 0


def test_memory_key_stable_for_catalog_facts():
    a = _cand(field_key="application.study_country", value="DE")
    b = _cand(field_key="application.study_country", value="DE", evidence_text="Germany")
    assert memory_key_for(a) == memory_key_for(b)
