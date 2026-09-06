"""Goal worker unit test: process_job can be called synchronously in tests.

Verifies that the worker pipeline is directly callable (no poll-loop dependency)
and produces a ready summary with ≤10-line brief.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pai.intelligences.goals.pipeline import _BRIEF_MAX_LINES, run_full_pipeline
from pai.intelligences.research.service import ResearchHit, ResearchResult


@pytest.mark.asyncio
async def test_pipeline_produces_ready_summary_synchronously():
    """Run all 4 stages synchronously (no background thread, no sleeping)."""
    call_count = 0

    async def rotating_response(**kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        if call_count == 1:
            resp.content = '{"requirements":["IELTS 6.5"],"options":["TU Berlin"],"eligibility_rules":[],"deadlines":["Jan 15"],"typical_cost":"0 EUR","notes":""}'
        elif call_count == 2:
            resp.content = '{"overall_fit":"moderate","strengths":["GPA 3.6"],"weaknesses":["no IELTS"],"meets_requirements":{},"notes":"needs IELTS"}'
        elif call_count == 3:
            resp.content = '{"gaps":[{"item":"IELTS","category":"test_score","blocking":true,"action":"Take IELTS"}]}'
        elif call_count == 4:
            resp.content = '{"plan":[{"step":"Register IELTS","priority":"high","timeline":"this week","depends_on":[]}]}'
        else:
            resp.content = "Goal: MS CS Germany\nFit: moderate\nBlocking: IELTS\nStep: Register IELTS"
        return resp

    from pai.intelligences.goals.pipeline import run_full_pipeline

    gateway = MagicMock()
    gateway.run = AsyncMock(side_effect=rotating_response)

    result = await run_full_pipeline(
        gateway,
        goal_type="admission",
        goal_title="MS CS in Germany",
        anchors={"target_country": "DE", "degree_level": "ms"},
        vault_snapshot={"educations": [{"gpa": 3.6}]},
        live_research=ResearchResult(
            ok=True,
            query="MS CS Germany",
            summary="TU Berlin IELTS 6.5",
            hits=[ResearchHit("TU Berlin", "https://example.edu", "IELTS 6.5")],
        ),
    )

    # Status check
    assert result["status"] == "ready"

    # Brief line count — the hard constraint from the spec
    brief_lines = [ln for ln in (result["counselor_brief"] or "").splitlines() if ln.strip()]
    assert len(brief_lines) <= _BRIEF_MAX_LINES, (
        f"counselor_brief exceeded {_BRIEF_MAX_LINES} lines: {len(brief_lines)}"
    )

    # Structural checks
    assert isinstance(result["gaps"], list)
    assert isinstance(result["plan"], list)
    assert "research" in result
    assert "assessment" in result
