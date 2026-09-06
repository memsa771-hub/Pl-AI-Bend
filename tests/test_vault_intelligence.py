"""Vault Intelligence: boosters, normalize, source registry."""

from __future__ import annotations

from pai.intelligences.vault.boosters import run_deterministic_boosters
from pai.intelligences.vault.merge import merge_candidates
from pai.intelligences.vault.normalize import normalize_candidates
from pai.intelligences.vault.service import VaultIntelligenceService
from pai.kernel.contracts.schemas import VaultCandidate


def test_boosters_catch_marks_stream_countries_and_cgpa():
    text = (
        "I completed FSc Pre-Medical 877/1100 in Islamabad. "
        "My CGPA later was 3.35. I want MS AI in Germany and China, targeting FAST and NUST."
    )
    cands, hits = run_deterministic_boosters(text, source_reference="m1")
    keys = {c.field_key for c in cands}
    assert "education.marks" in keys
    assert "education.stream" in keys or "education.program" in keys
    assert "education.gpa" in keys
    assert "application.study_country" in keys
    study = next(c for c in cands if c.field_key == "application.study_country")
    assert study.value == "DE, CN"
    regions = next(c for c in cands if c.field_key == "mobility.preferred_regions")
    assert regions.value == ["DE", "CN"]
    assert "application.target_universities" in keys
    assert "location.current_city" in keys
    assert "application.career_interest" in keys
    assert "education.marks" in hits


def test_boosters_extract_global_local_signals():
    text = "I live in Dubai, finishing A-Levels, targeting NYU Abu Dhabi."
    cands, _hits = run_deterministic_boosters(text, source_reference="m1")
    by_key = {c.field_key: c.value for c in cands}
    assert by_key["location.current_city"] == "Dubai"
    assert by_key["education.stream"] == "A-Levels"
    unis = by_key["application.target_universities"]
    assert any("NYU" in str(u).upper() for u in unis)


def test_boosters_funding_uses_budget_enum():
    cands, hits = run_deterministic_boosters("I have a limited budget", source_reference="m1")
    funding = next(c for c in cands if c.field_key == "finance.funding_status")
    assert funding.value == "limited"
    assert "finance.funding_status" in hits


def test_normalize_aliases_and_marks_string():
    raw = [
        VaultCandidate(
            field_key="education.cgpa",
            value=3.4,
            confidence=0.9,
            evidence_text="cgpa 3.4",
            source_reference="m1",
        ),
        VaultCandidate(
            field_key="education.marks",
            value="847/1100",
            confidence=0.9,
            evidence_text="847/1100",
            source_reference="m1",
        ),
        VaultCandidate(
            field_key="not.a.field",
            value="x",
            confidence=0.9,
            evidence_text="x",
            source_reference="m1",
        ),
    ]
    out = normalize_candidates(raw)
    keys = {c.field_key for c in out}
    assert "education.gpa" in keys
    assert "education.marks" in keys
    assert "not.a.field" not in keys
    marks = next(c for c in out if c.field_key == "education.marks")
    assert marks.value["obtained"] == 847.0
    assert marks.value["total"] == 1100.0


def test_merge_prefers_booster_on_marks():
    llm = VaultCandidate(
        field_key="education.marks",
        value={"obtained": 800, "total": 1100},
        confidence=0.85,
        evidence_text="approx",
        source_reference="m1",
        rationale_summary="llm",
    )
    booster = VaultCandidate(
        field_key="education.marks",
        value={"obtained": 877, "total": 1100},
        confidence=0.93,
        evidence_text="877/1100",
        source_reference="m1",
        rationale_summary="booster:marks_ratio",
    )
    merged = merge_candidates([llm], [booster])
    assert len(merged) == 1
    assert merged[0].value["obtained"] == 877


def test_vault_intel_registers_future_sources(test_settings):
    from pai.platform.llm.gateway import LLMGateway

    gw = LLMGateway(test_settings)
    svc = VaultIntelligenceService(gw)
    sources = svc.registered_sources()
    assert "chat" in sources
    assert "document" in sources
    assert "linkedin" in sources
    assert "social" in sources


def test_grounding_keeps_verbatim_and_drops_hallucination():
    from pai.intelligences.vault.ground import ground_candidates

    source = "I want to study locally in FAST"
    kept = VaultCandidate(
        field_key="application.target_universities",
        value=["FAST"],
        confidence=0.9,
        source_reference="m1",
        evidence_text="study locally in FAST",
    )
    fake = VaultCandidate(
        field_key="location.current_city",
        value="Mars",
        confidence=0.9,
        source_reference="m1",
        evidence_text="I live on Mars",
    )
    out = ground_candidates([kept, fake], source)
    assert [row.field_key for row in out] == ["application.target_universities"]


def test_grounding_drops_empty_evidence():
    from pai.intelligences.vault.ground import ground_candidates

    source = "I want Germany"
    blank = VaultCandidate(
        field_key="application.study_country",
        value="DE",
        confidence=0.9,
        source_reference="m1",
        evidence_text="",
    )
    assert ground_candidates([blank], source) == []


def test_normalize_keeps_other_potential_facts():
    raw = [
        VaultCandidate(
            field_key="memory.observed",
            value="Parents prefer Germany",
            confidence=0.8,
            evidence_text="my parents are more comfortable with Germany",
            source_reference="m1",
        ),
        VaultCandidate(
            field_key="OTHER_POTENTIAL_FACT",
            value="Brother thinks Germany is better",
            confidence=0.7,
            evidence_text="My brother thinks Germany is better",
            source_reference="m1",
        ),
    ]
    out = normalize_candidates(raw)
    assert len(out) == 2
    assert all(c.field_key == "memory.observed" for c in out)
    assert all(c.fact_type == "OTHER_POTENTIAL_FACT" for c in out)


def test_merge_keeps_distinct_jobs_and_observed():
    jobs = [
        VaultCandidate(
            field_key="career.work_history",
            value={"organization": "Acme", "title": "Intern"},
            confidence=0.9,
            evidence_text="Intern at Acme",
            source_reference="m1",
        ),
        VaultCandidate(
            field_key="career.work_history",
            value={"organization": "Beta", "title": "Tutor"},
            confidence=0.9,
            evidence_text="Tutor at Beta",
            source_reference="m1",
        ),
    ]
    observed = [
        VaultCandidate(
            field_key="memory.observed",
            value="Parents prefer Germany",
            confidence=0.8,
            evidence_text="parents are more comfortable with Germany",
            source_reference="m1",
        ),
        VaultCandidate(
            field_key="memory.observed",
            value="US tuition too expensive",
            confidence=0.8,
            evidence_text="US tuition is too expensive",
            source_reference="m1",
        ),
    ]
    merged = merge_candidates(jobs, observed)
    assert len(merged) == 4


def test_partition_keeps_negation_attribution_and_other_out_of_vault():
    from pai.kernel.evidence.candidate_eval import evaluate_candidate
    from pai.kernel.policy.verifier import policy_decision
    from pai.intelligences.vault.formation import partition_candidates

    negated = VaultCandidate(
        field_key="application.study_country",
        value="US",
        confidence=0.95,
        source_reference="m1",
        evidence_text="I don't want the US",
        assertion_status="negated",
    )
    maybe = VaultCandidate(
        field_key="application.study_country",
        value="US",
        confidence=0.9,
        source_reference="m1",
        evidence_text="If I get a scholarship, maybe I'll consider the US",
        assertion_status="hypothetical",
    )
    brother = VaultCandidate(
        field_key="application.study_country",
        value="DE",
        confidence=0.9,
        source_reference="m1",
        evidence_text="My brother thinks Germany is better",
        attributed_to="brother",
        assertion_status="explicit",
    )
    other = VaultCandidate(
        field_key="memory.observed",
        value="Parents prefer Germany",
        confidence=0.8,
        source_reference="m1",
        evidence_text="parents are more comfortable with Germany",
        fact_type="OTHER_POTENTIAL_FACT",
    )
    explicit = VaultCandidate(
        field_key="application.study_country",
        value="DE",
        confidence=0.95,
        source_reference="m1",
        evidence_text="I want Germany",
        assertion_status="explicit",
    )
    vault, observed = partition_candidates([negated, maybe, brother, other, explicit])
    assert [c.field_key for c in vault] == ["application.study_country"]
    assert vault[0].value == "DE"
    assert len(observed) == 4
    assert evaluate_candidate(negated, {"active_values": {}}).outcome == "reject"
    assert evaluate_candidate(brother, {"active_values": {}}).outcome == "reject"
    assert policy_decision(maybe) == "reject"

    inferred = VaultCandidate(
        field_key="preferences.preferred_language",
        value="en",
        confidence=0.86,
        source_reference="m1",
        evidence_text="English please",
        assertion_status="inferred",
    )
    result = evaluate_candidate(inferred, {"active_values": {}})
    assert result.outcome == "pending_confirmation"


def test_omnibus_prompt_is_recall_first_not_summarize():
    from pai.domains.student.vault.catalog import extraction_catalog_hint
    from pai.intelligences.vault.llm_extractor import _render

    text = _render(
        "omnibus.v1.jinja2",
        source="chat",
        source_reference="m1",
        document_type_hint="",
        known_facts=["budget = $15,000"],
        catalog_hint=extraction_catalog_hint(),
        text="I don't want the US",
    )
    assert "Do NOT summarize" in text
    assert "assertion_status" in text
    assert "memory.observed" in text
    assert "What should I remember" not in text
