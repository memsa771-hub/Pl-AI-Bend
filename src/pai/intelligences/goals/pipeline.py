"""Goal intelligence pipeline — four stages run sequentially, each isolated.

Research → Assessment → Gaps → Planning → counselor_brief

Design rules:
- Each stage reads structured JSON from the previous stage.
- No stage calls another stage conversationally.
- Stages accept mocked LLM responses in tests (pass fake_llm_response).
- counselor_brief is capped at 10 lines.
- Pipeline failure does not block chat.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pai.config import Settings
from pai.intelligences.research.service import ResearchResult, research_query
from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.schemas import LLMMessage

logger = logging.getLogger(__name__)

_BRIEF_MAX_LINES = 12


# ── Template configs ──────────────────────────────────────────────────────────

_GOAL_TYPE_GUIDANCE: dict[str, dict[str, str]] = {
    "admission": {
        "research_focus": (
            "university requirements, eligibility rules, IELTS/GRE cutoffs, "
            "application deadlines, scholarship options, tuition range"
        ),
        "assessment_focus": "GPA, test scores, SOP strength, relevant projects, work experience",
        "gaps_focus": "missing test scores, GPA shortfall, missing documents, deadline risks",
    },
    "job": {
        "research_focus": "job market demand, required skills, typical experience level, salary range",
        "assessment_focus": "skills match, work experience, portfolio, communication fit",
        "gaps_focus": "missing skills, portfolio gaps, experience gap, CV weaknesses",
    },
    "internship": {
        "research_focus": "internship availability, required skills, application timelines, stipend range",
        "assessment_focus": "skills, projects, availability, academic standing",
        "gaps_focus": "missing skills, project portfolio, availability mismatch",
    },
    "general": {
        "research_focus": "key requirements, options, typical steps to achieve this goal",
        "assessment_focus": "how well the student's current profile matches the goal",
        "gaps_focus": "missing items, blockers, weaknesses",
    },
}


def _goal_guidance(goal_type: str) -> dict[str, str]:
    return _GOAL_TYPE_GUIDANCE.get(goal_type, _GOAL_TYPE_GUIDANCE["general"])


# ── Stage helpers ─────────────────────────────────────────────────────────────

async def _llm_json(
    gateway: LLMGateway,
    system: str,
    user: str,
    *,
    max_tokens: int = 1200,
) -> dict[str, Any]:
    """Call LLM and parse JSON response. Returns empty dict on failure."""
    try:
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ]
        response = await gateway.run(
            task="goal_intelligence",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
        )
        content = getattr(response, "content", None) or ""
    except Exception:
        logger.exception("Goal intelligence LLM call failed")
        return {}
    parsed = _extract_json_object(content)
    if parsed is None:
        # Prose preamble, a refusal, or JSON truncated by max_tokens. Log what
        # actually came back — "Expecting value: line 1 column 1" alone tells
        # you nothing about which of those happened.
        preview = content.strip()[:200].replace("\n", " ")
        logger.warning(
            "Goal intelligence returned no parsable JSON (%d chars): %s",
            len(content),
            preview or "(empty response)",
        )
        return {}
    return parsed


def _extract_json_object(content: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a model response.

    The model sometimes wraps JSON in fences or leads with a sentence, so a bare
    json.loads on the whole string fails. Try the cleaned text first, then the
    first balanced {...} found anywhere in it.
    """
    raw = (content or "").strip()
    if not raw:
        return None
    unfenced = raw
    if unfenced.startswith("```"):
        # Split on the fence itself, not on newlines: ```json {"a": 1}``` is a
        # single line, and dropping line one would leave nothing to parse.
        unfenced = unfenced[3:]
        if unfenced.endswith("```"):
            unfenced = unfenced[: unfenced.rfind("```")]
        unfenced = unfenced.strip()
        if unfenced.lower().startswith("json"):
            unfenced = unfenced[4:].strip()
    # Always keep `raw` as a fallback so a mangled strip cannot lose the object.
    for candidate in (unfenced, _first_json_object(unfenced), _first_json_object(raw)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _first_json_object(text: str) -> str | None:
    """First balanced {...} in the text, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i, char in enumerate(text[start:], start):
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ── Stage 1: Research ─────────────────────────────────────────────────────────

async def run_research_stage(
    gateway: LLMGateway,
    *,
    goal_type: str,
    goal_title: str,
    anchors: dict[str, Any],
    settings: Settings | None = None,
    live_research: ResearchResult | None = None,
    structure: bool = True,
) -> dict[str, Any]:
    """
    Ground requirements/options/deadlines in live Research Intelligence, then
    structure those hits. Does not invent universities or costs from the LLM.
    """
    empty = {
        "requirements": [],
        "options": [],
        "eligibility_rules": [],
        "deadlines": [],
        "typical_cost": "unknown",
        "notes": "Live research unavailable.",
        "sources": [],
        "_error": True,
    }
    live = live_research
    if live is None:
        api_key = ((settings.tavily_api_key if settings is not None else "") or "").strip()
        if not api_key:
            empty["notes"] = "Live research unavailable: TAVILY_API_KEY is not configured."
            return empty
        query_parts = [goal_title, goal_type, _goal_guidance(goal_type)["research_focus"]]
        for key in ("program", "degree_level", "target_country", "role", "target_company"):
            val = anchors.get(key)
            if val:
                query_parts.append(str(val))
        live = await research_query(
            query=" ".join(query_parts),
            api_key=api_key,
            search_depth=settings.tavily_search_depth,
            max_results=settings.tavily_max_results,
        )
    if not live.ok or not (live.hits or live.summary):
        empty["notes"] = live.error or "No live research results."
        return empty

    sources = [{"title": h.title, "url": h.url} for h in live.hits]
    if not structure:
        return {"evidence": live.as_counselor_text(), "sources": sources}
    guidance = _goal_guidance(goal_type)
    system = (
        "You structure live web research for a counselor. Return ONLY valid JSON. "
        "Use only facts present in the provided search evidence. "
        "Do not invent universities, deadlines, costs, or requirements."
    )
    user = f"""Goal: {goal_title}
Type: {goal_type}
Research focus: {guidance['research_focus']}

Live search evidence:
{live.as_counselor_text()}

Return a JSON object with these keys:
- "requirements": list of key requirements found in the evidence (strings)
- "options": list of universities/companies/options found in the evidence (strings)
- "eligibility_rules": list of eligibility conditions found in the evidence (strings)
- "deadlines": list of deadlines or timelines found in the evidence (strings)
- "typical_cost": string estimate from the evidence, or "unknown"
- "notes": caveats grounded in the evidence (string)
"""
    result = await _llm_json(gateway, system, user, max_tokens=1200)
    if not result:
        return {
            **empty,
            "_error": True,
            "options": [h.title for h in live.hits[:3]],
            "notes": (live.summary or live.as_counselor_text())[:500],
            "sources": sources,
        }
    result["sources"] = sources
    result.pop("_error", None)
    return result


# ── Stage 2: Assessment ───────────────────────────────────────────────────────

async def run_assessment_stage(
    gateway: LLMGateway,
    *,
    research: dict[str, Any],
    vault_snapshot: dict[str, Any],
    goal_type: str,
    goal_title: str,
) -> dict[str, Any]:
    """
    Compare user's profile against research output.

    Input:  research JSON + vault snapshot
    Output: assessment JSON
    """
    guidance = _goal_guidance(goal_type)
    system = (
        "You are an expert counselor that assesses how well a student's profile "
        "matches a goal. Return ONLY valid JSON."
    )
    user = f"""Goal: {goal_title}
Type: {goal_type}
Assessment focus: {guidance['assessment_focus']}

Research summary:
{json.dumps(research, ensure_ascii=False, indent=2)}

Student profile:
{json.dumps(vault_snapshot, ensure_ascii=False, indent=2)}

Return a JSON object with these keys:
- "overall_fit": one of "strong" | "moderate" | "weak" | "unknown"
- "strengths": list of student strengths relevant to this goal (strings)
- "weaknesses": list of student weaknesses (strings)
- "meets_requirements": object mapping each requirement to true/false/null
- "notes": brief assessment summary (string, max 3 sentences)
- "stated_alignment": one of "aligned" | "mismatch" | "possibly_pressured"
  (pressured = peer pressure or copying others rather than profile fit; mismatch = strengths/constraints point elsewhere)
- "counselor_recommendation": 1-2 sentences on what you would advise if alignment is not strong; empty string if aligned
- "alternative_paths": list of related fields or program types that fit strengths + constraints (empty if aligned)
"""
    result = await _llm_json(gateway, system, user, max_tokens=800)
    if not result:
        return {
            "overall_fit": "unknown",
            "strengths": [],
            "weaknesses": [],
            "meets_requirements": {},
            "notes": "Assessment unavailable.",
            "stated_alignment": "unknown",
            "counselor_recommendation": "",
            "alternative_paths": [],
            "_error": True,
        }
    return result


# ── Stage 3: Gaps ─────────────────────────────────────────────────────────────

async def run_gaps_stage(
    gateway: LLMGateway,
    *,
    assessment: dict[str, Any],
    goal_type: str,
    goal_title: str,
) -> list[dict[str, Any]]:
    """
    Identify missing items for this goal.

    Input:  assessment JSON
    Output: list of gap objects
    """
    guidance = _goal_guidance(goal_type)
    system = (
        "You are an expert counselor that identifies specific gaps a student "
        "must address to achieve their goal. Return ONLY valid JSON."
    )
    user = f"""Goal: {goal_title}
Type: {goal_type}
Gaps focus: {guidance['gaps_focus']}

Assessment:
{json.dumps(assessment, ensure_ascii=False, indent=2)}

Return a JSON object with key "gaps" containing a list.
Each gap object must have:
- "item": what is missing (string)
- "category": one of "test_score" | "document" | "experience" | "skill" | "deadline" | "other"
- "blocking": true if this gap blocks the application, false if it's advisory
- "action": recommended action to close this gap (string)
"""
    result = await _llm_json(gateway, system, user, max_tokens=600)
    gaps = result.get("gaps") if isinstance(result, dict) else None
    if not isinstance(gaps, list):
        return []
    return gaps


# ── Stage 4: Planning ─────────────────────────────────────────────────────────

async def run_planning_stage(
    gateway: LLMGateway,
    *,
    gaps: list[dict[str, Any]],
    research: dict[str, Any],
    goal_type: str,
    goal_title: str,
) -> list[dict[str, Any]]:
    """
    Create an ordered action plan from gaps + research.

    Input:  gaps list + research JSON
    Output: list of plan step objects
    """
    system = (
        "You are an expert counselor that creates concise, ordered action plans "
        "for students. Return ONLY valid JSON."
    )
    user = f"""Goal: {goal_title}
Type: {goal_type}

Gaps:
{json.dumps(gaps, ensure_ascii=False, indent=2)}

Key deadlines/options from research:
{json.dumps(research.get('deadlines', []), ensure_ascii=False)}

Return a JSON object with key "plan" containing an ordered list of steps.
Each step must have:
- "step": concise action description (string, ≤ 120 chars)
- "priority": "high" | "medium" | "low"
- "timeline": rough timeline, e.g. "this week" | "1-2 months" | "before deadline"
- "depends_on": list of step indices (0-based) this step depends on (empty list if none)
"""
    result = await _llm_json(gateway, system, user, max_tokens=800)
    plan = result.get("plan") if isinstance(result, dict) else None
    if not isinstance(plan, list):
        return []
    return plan


# ── Counselor brief ───────────────────────────────────────────────────────────

async def build_counselor_brief(
    gateway: LLMGateway,
    *,
    goal_title: str,
    goal_type: str,
    research: dict[str, Any],
    assessment: dict[str, Any],
    gaps: list[dict[str, Any]],
    plan: list[dict[str, Any]],
) -> str:
    """
    Produce the compact counselor_brief (max 10 lines).
    This is what the counselor reads every turn.
    """
    system = (
        "You produce a concise goal intelligence brief for a counselor. "
        "Maximum 12 lines. Plain text, no markdown headers. "
        "Cover: stated goal, fit, whether it matches the student's profile vs constraints/pressure, "
        "Path 1 counselor recommendation if alignment is weak, Path 2 honor stated goal, "
        "top 2 gaps, top 3 next steps."
    )
    user = f"""Goal: {goal_title}
Type: {goal_type}

Overall fit: {assessment.get('overall_fit', 'unknown')}
Alignment: {assessment.get('stated_alignment', 'unknown')}
Strengths: {', '.join((assessment.get('strengths') or [])[:3])}
Weaknesses: {', '.join((assessment.get('weaknesses') or [])[:3])}
Counselor recommendation: {assessment.get('counselor_recommendation') or '(none)'}
Alternative paths: {', '.join((assessment.get('alternative_paths') or [])[:4]) or '(none)'}
Blocking gaps: {', '.join(g['item'] for g in gaps if g.get('blocking'))[:3]}
Top plan steps: {', '.join(s['step'] for s in plan[:3])}

Write a 6–12 line counselor brief. If alignment is mismatch or possibly_pressured, Path 1 must be the counselor rec and Path 2 must keep the student's stated goal. Be specific.
"""
    try:
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ]
        resp = await gateway.run(task="goal_intelligence", messages=messages, max_tokens=450, temperature=0.3)
        brief = getattr(resp, "content", None) or ""
        lines = [ln for ln in (brief or "").splitlines() if ln.strip()]
        return "\n".join(lines[:_BRIEF_MAX_LINES])
    except Exception:
        logger.exception("Failed to build counselor brief")
        fit = assessment.get("overall_fit", "unknown")
        blocking = [g["item"] for g in gaps if g.get("blocking")][:2]
        steps = [s["step"] for s in plan[:3]]
        rec = assessment.get("counselor_recommendation") or ""
        alts = assessment.get("alternative_paths") or []
        align = assessment.get("stated_alignment") or ""
        parts = [f"Goal: {goal_title}", f"Fit: {fit}"]
        if align and align not in ("aligned", "unknown"):
            parts.append(f"Alignment: {align}")
        if rec:
            parts.append("Path 1: " + str(rec)[:180])
        if alts:
            parts.append("Also consider: " + "; ".join(str(a) for a in alts[:3]))
            parts.append("Path 2: honor stated goal inside constraints if they insist.")
        if blocking:
            parts.append("Blocking gaps: " + "; ".join(blocking))
        if steps:
            parts.append("Next steps: " + "; ".join(steps))
        return "\n".join(parts[:_BRIEF_MAX_LINES])


# ── Full pipeline ─────────────────────────────────────────────────────────────

async def run_full_pipeline(
    gateway: LLMGateway,
    *,
    goal_type: str,
    goal_title: str,
    anchors: dict[str, Any],
    vault_snapshot: dict[str, Any],
    settings: Settings | None = None,
    live_research: ResearchResult | None = None,
) -> dict[str, Any]:
    """
    Run all 4 stages sequentially and return the full intelligence object.

    Failures in individual stages produce partial output — pipeline continues.
    """
    research = await run_research_stage(
        gateway,
        goal_type=goal_type,
        goal_title=goal_title,
        anchors=anchors,
        settings=settings,
        live_research=live_research,
        structure=not (settings and settings.enable_integrated_goal_analysis),
    )
    if settings and settings.enable_integrated_goal_analysis:
        from pai.intelligences.goals.integrated import analyze_goal

        return await analyze_goal(
            gateway, settings=settings, goal_type=goal_type, goal_title=goal_title,
            vault_snapshot=vault_snapshot, research=research,
        )
    assessment = await run_assessment_stage(
        gateway,
        research=research,
        vault_snapshot=vault_snapshot,
        goal_type=goal_type,
        goal_title=goal_title,
    )
    gaps = await run_gaps_stage(
        gateway, assessment=assessment, goal_type=goal_type, goal_title=goal_title
    )
    plan = await run_planning_stage(
        gateway, gaps=gaps, research=research, goal_type=goal_type, goal_title=goal_title
    )
    brief = await build_counselor_brief(
        gateway,
        goal_title=goal_title,
        goal_type=goal_type,
        research=research,
        assessment=assessment,
        gaps=gaps,
        plan=plan,
    )
    any_error = any(
        (isinstance(s, dict) and s.get("_error"))
        for s in [research, assessment]
    )
    status = "partial" if any_error else "ready"
    return {
        "research": research,
        "assessment": assessment,
        "gaps": gaps,
        "plan": plan,
        "counselor_brief": brief,
        "status": status,
        "freshness": {
            "computed_at": datetime.now(UTC).isoformat(),
        },
    }
