"""Enumeration is deterministic; conversational selection is semantic."""
from datetime import UTC, datetime, timedelta
from pai.intelligences.counselor.discovery import select_discovery_candidates
from pai.intelligences.counselor.profile_depth import DepthGap
from pai.domains.student.vault.catalog import VAULT_CATALOG

def test_enumeration_does_not_choose_a_question_from_keywords():
    first = select_discovery_candidates(missing_important=["finance.funding_status"],
        message="Germany admission")
    second = select_discovery_candidates(missing_important=["finance.funding_status"],
        message="我不需要申请大学")
    assert first == second
    assert first.top is None
    assert [c.field_key for c in first.runners_up] == ["finance.funding_status"]

def test_recent_question_is_suppressed_until_cooldown_expires():
    now = datetime.now(UTC)
    for age, expected in [(1, []), (4, ["finance.funding_status"])]:
        result = select_discovery_candidates(missing_important=["finance.funding_status"],
            recently_asked_field_key="finance.funding_status",
            recently_asked_at=now-timedelta(days=age), now=now)
        assert [c.field_key for c in result.runners_up] == expected

def test_derived_noneditable_and_unknown_fields_are_excluded():
    keys = [key for key, f in VAULT_CATALOG.items() if f.derived or not f.editable]
    result = select_discovery_candidates(missing_critical=keys+["unknown.field"])
    assert result.runners_up == []

def test_duplicate_candidates_are_not_repeated():
    result = select_discovery_candidates(missing_critical=["finance.funding_status"],
        missing_important=["finance.funding_status"])
    assert len(result.runners_up) == 1

def test_actual_record_gap_preserves_its_identity_and_explanation():
    gap = DepthGap("education.record.q1.degree", "education", "qualification",
        "Original qualification name is unknown")
    result = select_discovery_candidates(depth_gaps=[gap])
    assert result.top is None
    assert result.runners_up[0].field_key == gap.key
    assert result.runners_up[0].reason_text == gap.reason
