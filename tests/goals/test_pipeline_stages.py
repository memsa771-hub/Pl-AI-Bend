"""Pipeline stage isolation tests — no LLM API calls.

Each stage is tested with a fixed response so tests are deterministic and
do not burn API budget or flake on network issues.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pai.intelligences.goals.pipeline import (
    _BRIEF_MAX_LINES,
    build_counselor_brief,
    run_assessment_stage,
    run_full_pipeline,
    run_gaps_stage,
    run_planning_stage,
    run_research_stage,
)
from pai.intelligences.research.service import ResearchHit, ResearchResult


def _live_research() -> ResearchResult:
    return ResearchResult(
        ok=True,
        query="MS CS Germany",
        summary="TU Berlin MS CS typically wants IELTS 6.5 and a bachelor degree.",
        hits=[
            ResearchHit(
                title="TU Berlin",
                url="https://example.edu/cs",
                snippet="IELTS 6.5. Bachelor required. Deadline January 15. Tuition 0-500 EUR.",
            )
        ],
    )


def _fake_gateway(response_json: dict | list | None = None, text: str = "") -> MagicMock:
    """Create a gateway mock that returns a fixed JSON or text."""
    import json

    gateway = MagicMock()
    if response_json is not None:
        content = json.dumps(response_json)
    else:
        content = text

    async def fake_run(**kwargs):
        resp = MagicMock()
        resp.content = content
        return resp

    gateway.run = AsyncMock(side_effect=fake_run)
    return gateway


# ── Stage 1: Research ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_research_stage_returns_structured_output():
    gateway = _fake_gateway(
        response_json={
            "requirements": ["IELTS 6.5", "Bachelor degree"],
            "options": ["TU Berlin", "TU Munich"],
            "eligibility_rules": ["min GPA 3.0"],
            "deadlines": ["January 15 for summer"],
            "typical_cost": "0–500 EUR/year",
            "notes": "Strong STEM focus.",
        }
    )
    result = await run_research_stage(
        gateway,
        goal_type="admission",
        goal_title="MS CS in Germany",
        anchors={"target_country": "DE", "degree_level": "ms"},
        live_research=_live_research(),
    )
    assert "requirements" in result
    assert isinstance(result["requirements"], list)
    assert len(result["requirements"]) == 2
    assert "_error" not in result


@pytest.mark.asyncio
async def test_research_stage_handles_llm_failure():
    gateway = MagicMock()

    async def failing_run(**kwargs):
        raise RuntimeError("LLM down")

    gateway.run = AsyncMock(side_effect=failing_run)
    result = await run_research_stage(
        gateway,
        goal_type="admission",
        goal_title="MS CS in Germany",
        anchors={},
    )
    assert result.get("_error") is True
    assert "requirements" in result


# ── Stage 2: Assessment ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assessment_stage_flags_ielts_gap():
    gateway = _fake_gateway(
        response_json={
            "overall_fit": "moderate",
            "strengths": ["strong GPA"],
            "weaknesses": ["no IELTS score"],
            "meets_requirements": {"IELTS 6.5": False},
            "notes": "Good academic fit but needs IELTS.",
        }
    )
    result = await run_assessment_stage(
        gateway,
        research={"requirements": ["IELTS 6.5"], "options": []},
        vault_snapshot={"educations": [{"gpa": 3.6}]},
        goal_type="admission",
        goal_title="MS CS in Germany",
    )
    assert result["overall_fit"] == "moderate"
    assert "IELTS 6.5" in result["meets_requirements"]
    assert result["meets_requirements"]["IELTS 6.5"] is False


# ── Stage 3: Gaps ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gaps_stage_flags_missing_ielts():
    """The spec's canonical test: missing IELTS must appear as a blocking gap."""
    gateway = _fake_gateway(
        response_json={
            "gaps": [
                {
                    "item": "IELTS score",
                    "category": "test_score",
                    "blocking": True,
                    "action": "Register for IELTS and target 6.5+",
                }
            ]
        }
    )
    assessment_output = {"ielts": {"required": 6.5, "user_has": None}, "overall_fit": "moderate"}
    gaps = await run_gaps_stage(
        gateway,
        assessment=assessment_output,
        goal_type="admission",
        goal_title="MS CS in Germany",
    )
    assert any(
        g.get("category") == "test_score" and g.get("blocking") is True for g in gaps
    ), "Expected a blocking test_score gap for missing IELTS"


@pytest.mark.asyncio
async def test_gaps_stage_returns_empty_list_on_failure():
    gateway = MagicMock()

    async def failing_run(**kwargs):
        raise RuntimeError("timeout")

    gateway.run = AsyncMock(side_effect=failing_run)
    gaps = await run_gaps_stage(
        gateway,
        assessment={},
        goal_type="admission",
        goal_title="MS CS in Germany",
    )
    assert gaps == []


# ── Stage 4: Planning ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planning_stage_returns_ordered_steps():
    gateway = _fake_gateway(
        response_json={
            "plan": [
                {"step": "Register for IELTS", "priority": "high", "timeline": "this week", "depends_on": []},
                {"step": "Request official transcripts", "priority": "medium", "timeline": "1-2 months", "depends_on": []},
                {"step": "Draft SOP", "priority": "high", "timeline": "2 months", "depends_on": [0]},
            ]
        }
    )
    plan = await run_planning_stage(
        gateway,
        gaps=[{"item": "IELTS", "blocking": True}],
        research={"deadlines": ["January 15"]},
        goal_type="admission",
        goal_title="MS CS in Germany",
    )
    assert len(plan) == 3
    assert plan[0]["priority"] == "high"


# ── Counselor brief ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_counselor_brief_max_lines():
    """Brief must never exceed _BRIEF_MAX_LINES lines — hard constraint from spec."""
    long_brief = "\n".join([f"Line {i}: some counselor note about the goal" for i in range(20)])
    gateway = MagicMock()

    async def fake_run(**kwargs):
        resp = MagicMock()
        resp.content = long_brief
        return resp

    gateway.run = AsyncMock(side_effect=fake_run)
    brief = await build_counselor_brief(
        gateway,
        goal_title="MS CS in Germany",
        goal_type="admission",
        research={},
        assessment={"overall_fit": "moderate", "strengths": [], "weaknesses": []},
        gaps=[],
        plan=[],
    )
    actual_lines = [ln for ln in brief.splitlines() if ln.strip()]
    assert len(actual_lines) <= _BRIEF_MAX_LINES, (
        f"Brief exceeded {_BRIEF_MAX_LINES} lines: got {len(actual_lines)}"
    )


# ── Full pipeline ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_pipeline_produces_ready_summary():
    """Full pipeline with mocked LLM must produce status='ready' and a brief."""
    call_count = 0

    async def rotating_response(**kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        # Research
        if call_count == 1:
            resp.content = '{"requirements":["IELTS 6.5"],"options":["TU Berlin"],"eligibility_rules":[],"deadlines":["Jan 15"],"typical_cost":"0 EUR","notes":""}'
        # Assessment
        elif call_count == 2:
            resp.content = '{"overall_fit":"moderate","strengths":["Good GPA"],"weaknesses":["No IELTS"],"meets_requirements":{},"notes":"needs IELTS"}'
        # Gaps
        elif call_count == 3:
            resp.content = '{"gaps":[{"item":"IELTS","category":"test_score","blocking":true,"action":"Take IELTS"}]}'
        # Planning
        elif call_count == 4:
            resp.content = '{"plan":[{"step":"Take IELTS","priority":"high","timeline":"this month","depends_on":[]}]}'
        # Brief
        else:
            resp.content = "Goal: MS CS Germany\nFit: moderate\nBlocking: IELTS missing\nNext: Register for IELTS"
        return resp

    gateway = MagicMock()
    gateway.run = AsyncMock(side_effect=rotating_response)

    result = await run_full_pipeline(
        gateway,
        goal_type="admission",
        goal_title="MS CS in Germany",
        anchors={"target_country": "DE", "degree_level": "ms"},
        vault_snapshot={"educations": [{"gpa": 3.6}]},
        live_research=_live_research(),
    )
    assert result["status"] == "ready"
    assert result["counselor_brief"]
    brief_lines = [ln for ln in result["counselor_brief"].splitlines() if ln.strip()]
    assert len(brief_lines) <= _BRIEF_MAX_LINES
    assert isinstance(result["gaps"], list)
    assert isinstance(result["plan"], list)
