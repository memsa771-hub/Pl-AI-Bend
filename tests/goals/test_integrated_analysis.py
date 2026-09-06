from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from pai.intelligences.goals.integrated import IntegratedAnalysis, analyze_goal
from pai.intelligences.goals.pipeline import run_full_pipeline
from pai.intelligences.research.service import ResearchHit, ResearchResult
from pai.platform.llm.providers.openai_responses import strict_schema


def analysis(**updates):
    data = {
        "research": {
            "requirements": [],
            "options": [],
            "eligibility_rules": [],
            "deadlines": [],
            "typical_cost": "unknown",
            "notes": "Verify details",
        },
        "assessment": {
            "overall_fit": "unknown",
            "strengths": [],
            "weaknesses": [],
            "meets_requirements": [{"requirement": "IELTS", "met": None}],
            "notes": "Score unknown",
            "stated_alignment": "unknown",
            "counselor_recommendation": "",
            "alternative_paths": [],
        },
        "gaps": [
            {
                "item": "Score",
                "category": "test_score",
                "blocking": False,
                "action": "Confirm score",
            }
        ],
        "plan": [
            {"step": "Confirm score", "priority": "high", "timeline": "this week", "depends_on": []}
        ],
        "counselor_brief": "Confirm the student's score before assessing eligibility.",
        "confidence": 0.8,
        "contradictory_evidence": False,
    }
    return IntegratedAnalysis.model_validate({**data, **updates})


async def test_integrated_full_pipeline_uses_one_call_after_evidence(test_settings):
    gateway = AsyncMock()
    gateway.run.return_value = analysis()
    settings = test_settings.model_copy(update={"enable_integrated_goal_analysis": True})
    live = ResearchResult(
        ok=True,
        query="admission",
        hits=[ResearchHit(title="Official", url="https://example.edu", snippet="Score required")],
    )
    result = await run_full_pipeline(
        gateway,
        goal_type="admission",
        goal_title="MS",
        anchors={},
        vault_snapshot={},
        settings=settings,
        live_research=live,
    )
    assert gateway.run.await_count == 1
    assert gateway.run.call_args.kwargs["output_schema"] is IntegratedAnalysis
    assert result["assessment"]["meets_requirements"] == {"IELTS": None}
    assert result["research"]["sources"][0]["url"] == "https://example.edu"
    assert result["status"] == "ready"
    strict_schema(IntegratedAnalysis.model_json_schema())


@pytest.mark.parametrize("updates", [{"confidence": 0.3}, {"contradictory_evidence": True}])
async def test_one_bounded_escalation(test_settings, updates):
    gateway = AsyncMock()
    gateway.run.side_effect = [analysis(**updates), analysis()]
    result = await analyze_goal(
        gateway,
        settings=test_settings,
        goal_title="MS",
        goal_type="admission",
        vault_snapshot={},
        research={"sources": []},
    )
    assert [call.kwargs["task"] for call in gateway.run.call_args_list] == [
        "goal_intelligence",
        "complex_analysis",
    ]
    assert result["freshness"]["escalated"] is True


async def test_missing_evidence_stays_partial_without_costly_retry(test_settings):
    gateway = AsyncMock()
    gateway.run.return_value = analysis(confidence=0.2)
    result = await analyze_goal(
        gateway,
        settings=test_settings,
        goal_title="MS",
        goal_type="admission",
        vault_snapshot={},
        research={"_error": True},
    )
    assert result["status"] == "partial"
    assert result["research"]["typical_cost"] == "unknown"
    assert gateway.run.await_count == 1


async def test_escalation_failure_keeps_validated_partial_result(test_settings):
    gateway = AsyncMock()
    gateway.run.side_effect = [analysis(confidence=0.3), RuntimeError("unavailable")]
    result = await analyze_goal(
        gateway,
        settings=test_settings,
        goal_title="MS",
        goal_type="admission",
        vault_snapshot={},
        research={},
    )
    assert result["status"] == "partial"
    assert result["plan"]


def test_invalid_plan_dependencies_rejected():
    with pytest.raises(ValidationError):
        analysis(plan=[{"step": "Apply", "priority": "high", "timeline": "now", "depends_on": [0]}])
