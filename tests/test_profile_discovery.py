"""Profile Discovery / Gap Selection: deterministic ranking, no LLM call."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pai.intelligences.counselor.context import CounselorContext
from pai.intelligences.counselor.discovery import (
    explain,
    score_field,
    select_discovery_candidates,
)
from pai.intelligences.counselor.profile_depth import DepthGap
from pai.domains.student.vault.catalog import VAULT_CATALOG


def _bachelor_depth_gap() -> DepthGap:
    return DepthGap(
        key="education.level.bachelor",
        section="education",
        label="bachelor's degree",
        reason="their bachelor's degree isn't on file yet — needed for transcripts and SOPs",
        impact=0.7,
    )


def test_goal_relevant_field_outscores_irrelevant_critical_field():
    """Doc §6 example: comparing fields, a goal-relevant Important field can
    outrank an unrelated Critical one."""
    result = select_discovery_candidates(
        missing_critical=["demographics.gender"],
        missing_important=["finance.funding_status"],
        message="Should I do my master's in Germany or the UK?",
        goal_type="admission",
    )
    assert result.top is not None
    assert result.top.field_key == "finance.funding_status"


def test_message_relevance_picks_the_field_the_student_is_actually_asking_about():
    result = select_discovery_candidates(
        missing_critical=["location.current_country"],
        missing_important=["career.projects"],
        message="I built two ML projects and want to compare AI vs cybersecurity",
    )
    assert result.top is not None
    assert result.top.field_key == "career.projects"


def test_recently_asked_field_is_suppressed():
    now = datetime.now(UTC)
    baseline = select_discovery_candidates(
        missing_important=["finance.funding_status"],
        message="",
    )
    assert baseline.top is not None
    assert baseline.top.field_key == "finance.funding_status"

    suppressed = select_discovery_candidates(
        missing_important=["finance.funding_status"],
        message="",
        recently_asked_field_key="finance.funding_status",
        recently_asked_at=now - timedelta(hours=1),
        now=now,
    )
    assert suppressed.top is None or suppressed.top.field_key != "finance.funding_status"


def test_old_recently_asked_field_is_no_longer_suppressed():
    now = datetime.now(UTC)
    result = select_discovery_candidates(
        missing_important=["finance.funding_status"],
        message="",
        recently_asked_field_key="finance.funding_status",
        recently_asked_at=now - timedelta(days=10),
        now=now,
    )
    assert result.top is not None
    assert result.top.field_key == "finance.funding_status"


def test_known_facts_remove_a_field_from_candidates():
    result = select_discovery_candidates(
        missing_critical=["location.current_country"],
        known_facts=["Current country: Pakistan"],
    )
    assert result.top is None


def test_derived_and_non_editable_fields_are_never_candidates():
    # education.records and application.goals are derived/non-editable.
    result = select_discovery_candidates(
        missing_critical=["education.records"],
        missing_important=["application.goals"],
    )
    assert result.top is None
    assert result.missing_important == []


def test_critical_outranks_enrichment_absent_other_signal():
    result = select_discovery_candidates(
        missing_critical=["location.current_country"],
        missing_enrichment=["social.linkedin_url"],
        message="",
    )
    assert result.top is not None
    assert result.top.field_key == "location.current_country"


def test_missing_important_and_enrichment_lists_are_capped():
    important_keys = [
        k for k, f in VAULT_CATALOG.items() if f.priority == "I" and f.editable and not f.derived
    ]
    enrichment_keys = [
        k for k, f in VAULT_CATALOG.items() if f.priority == "E" and f.editable and not f.derived
    ]
    result = select_discovery_candidates(
        missing_important=important_keys,
        missing_enrichment=enrichment_keys,
    )
    assert len(result.missing_important) <= 4
    assert len(result.enrichment_opportunities) <= 2


def test_score_field_formula_matches_expected_ordering():
    critical = VAULT_CATALOG["location.current_country"]
    enrichment = VAULT_CATALOG["social.linkedin_url"]
    c_score = score_field(
        critical, message="", goal_type=None, is_recently_asked=False
    )
    e_score = score_field(
        enrichment, message="", goal_type=None, is_recently_asked=False
    )
    assert c_score.score > e_score.score


def test_recently_asked_penalty_can_flip_ordering():
    field_obj = VAULT_CATALOG["location.current_country"]
    fresh = score_field(field_obj, message="", goal_type=None, is_recently_asked=False)
    penalized = score_field(field_obj, message="", goal_type=None, is_recently_asked=True)
    assert penalized.score < fresh.score
    assert penalized.score < 0


def test_explain_mentions_message_relevance():
    result = select_discovery_candidates(
        missing_important=["finance.funding_status"],
        message="what's a realistic budget for studying in Germany",
    )
    assert result.top is not None
    line = explain(result.top)
    assert "funding status" in line
    assert "relevant to what you just asked" in line


def test_no_candidates_returns_empty_result():
    result = select_discovery_candidates()
    assert result.top is None
    assert result.runners_up == []
    assert result.missing_important == []
    assert result.enrichment_opportunities == []


def test_profile_block_renders_top_discovery_candidate_over_flat_gap_list():
    ctx = CounselorContext(
        person_id="p1",
        known_facts=["Current goal (pursuing): MS CS in Germany"],
        missing_critical_fields=["demographics.gender", "location.current_country"],
        top_discovery_candidate="finance.funding_status",
        discovery_reason="funding status — relevant to your active goal",
    )
    block = ctx.profile_block()
    assert "gaps: funding status — relevant to your active goal" in block
    assert "demographics.gender" not in block


def test_profile_block_falls_back_to_flat_gap_list_without_discovery_candidate():
    ctx = CounselorContext(
        person_id="p1",
        missing_critical_fields=["demographics.gender"],
    )
    block = ctx.profile_block()
    assert "gaps: demographics.gender" in block


def test_depth_gap_never_surfaces_upfront_when_irrelevant():
    result = select_discovery_candidates(
        depth_gaps=[_bachelor_depth_gap()],
        message="hey, good to be back",
    )
    assert result.top is None


def test_depth_gap_surfaces_when_message_is_relevant():
    result = select_discovery_candidates(
        depth_gaps=[_bachelor_depth_gap()],
        message="can you pull together my degree and transcript history",
    )
    assert result.top is not None
    assert result.top.kind == "depth"
    assert result.top.field_key == "education.level.bachelor"


def test_depth_gap_surfaces_via_active_goal_relevance():
    result = select_discovery_candidates(
        depth_gaps=[_bachelor_depth_gap()],
        message="",
        goal_type="admission",
    )
    assert result.top is not None
    assert result.top.kind == "depth"


def test_explain_uses_depth_reason_text():
    result = select_discovery_candidates(
        depth_gaps=[_bachelor_depth_gap()],
        message="tell me about my degree",
        goal_type="admission",
    )
    assert result.top is not None
    assert explain(result.top) == (
        "their bachelor's degree isn't on file yet — needed for transcripts and SOPs"
    )


def test_relevant_catalog_field_and_depth_gap_coexist_in_ranking():
    # A message relevant to education surfaces something useful, and depth gaps
    # participate without crashing the flat-field path.
    result = select_discovery_candidates(
        missing_important=["finance.funding_status"],
        depth_gaps=[_bachelor_depth_gap()],
        message="what gpa and degree records matter for a funded master's",
        goal_type="admission",
    )
    assert result.top is not None
