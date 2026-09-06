"""Goal-centric API routes.

GET  /api/v1/goals                 — list all non-archived goals
GET  /api/v1/goals/{id}            — goal detail + intelligence summary
POST /api/v1/goals/{id}/activate   — make this goal active in the current conversation
GET  /api/v1/goals/active          — current active goal for the default conversation
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.interfaces.api.dependencies import get_db, require_onboarding_complete
from pai.interfaces.api.schemas import ApiErrorResponse, success
from pai.domains.conversations.service import get_or_create_person_conversation
from pai.config import Settings, get_settings
from pai.domains.goals.service import (
    activate_goal,
    get_conversation_active_goal,
    get_goal_by_id,
    get_goal_intelligence,
    list_goals,
    switch_conversation_active_goal,
)
from pai.domains.goals.models import Goal, GoalIntelligence

goals_router = APIRouter(prefix="/api/v1/goals", tags=["goals"])

_AUTH_ERRORS = {
    401: {"model": ApiErrorResponse, "description": "Missing/invalid Bearer token"},
    403: {"model": ApiErrorResponse, "description": "Onboarding not completed"},
    404: {"model": ApiErrorResponse, "description": "Goal not found"},
}


def _serialize_goal(goal: Goal, intel: GoalIntelligence | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(goal.id),
        "goalType": goal.goal_type,
        "title": goal.title,
        "description": goal.description,
        "lifecycleStatus": goal.lifecycle_status,
        "intelligenceStatus": goal.intelligence_status,
        "degreeLevel": goal.degree_level,
        "program": goal.program,
        "targetCountry": goal.target_country,
        "targetCompany": goal.target_company,
        "role": goal.role,
        "intakeYear": goal.intake_year,
        "intakeTerm": goal.intake_term,
        "budgetRange": goal.budget_range,
        "anchors": goal.anchors or {},
        "confidence": goal.confidence,
        "createdAt": goal.created_at.isoformat() if goal.created_at else None,
        "updatedAt": goal.updated_at.isoformat() if goal.updated_at else None,
    }
    if intel is not None:
        gaps = intel.gaps or []
        base["intelligence"] = {
            "status": intel.status,
            "counselorBrief": intel.counselor_brief,
            "gaps": [
                {
                    "item": g.get("item"),
                    "category": g.get("category"),
                    "blocking": g.get("blocking", False),
                    "action": g.get("action"),
                }
                for g in gaps
            ],
            "planStepCount": len(intel.plan or []),
            "overallFit": (intel.assessment or {}).get("overall_fit"),
            "updatedAt": intel.updated_at.isoformat() if intel.updated_at else None,
        }
    else:
        base["intelligence"] = None
    return base


@goals_router.get(
    "",
    summary="List goals",
    responses=_AUTH_ERRORS,
)
async def list_student_goals(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
    include_archived: bool = Query(False, description="Include archived goals"),
) -> JSONResponse:
    goals = await list_goals(session, person.id, include_archived=include_archived)
    goal_ids = [g.id for g in goals]
    intel_by_goal: dict = {}
    if goal_ids:
        intel_rows = await session.execute(
            select(GoalIntelligence).where(GoalIntelligence.goal_id.in_(goal_ids))
        )
        intel_by_goal = {row.goal_id: row for row in intel_rows.scalars().all()}
    return JSONResponse(
        content=success(
            {
                "items": [
                    _serialize_goal(g, intel_by_goal.get(g.id)) for g in goals
                ],
                "total": len(goals),
            }
        )
    )


@goals_router.get(
    "/active",
    summary="Get the active goal",
    responses=_AUTH_ERRORS,
)
async def get_active_goal_endpoint(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    conv = await get_or_create_person_conversation(session, person, settings=settings)
    goal = await get_conversation_active_goal(session, conv.id, person.id)
    if goal is None:
        return JSONResponse(content=success({"goal": None}))
    intel = await get_goal_intelligence(session, goal.id)
    return JSONResponse(content=success({"goal": _serialize_goal(goal, intel)}))


@goals_router.get(
    "/{goal_id}",
    summary="Goal detail",
    responses=_AUTH_ERRORS,
)
async def get_goal_detail(
    goal_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    from pai.kernel.errors import AuthError

    goal = await get_goal_by_id(session, goal_id, person.id)
    if goal is None:
        raise AuthError(code="GOAL_NOT_FOUND", message="Goal not found.", status_code=404)
    intel = await get_goal_intelligence(session, goal.id)
    return JSONResponse(content=success({"goal": _serialize_goal(goal, intel)}))


@goals_router.post(
    "/{goal_id}/activate",
    summary="Activate a goal",
    responses=_AUTH_ERRORS,
)
async def activate_goal_endpoint(
    goal_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    from pai.kernel.errors import AuthError
    from pai.domains.goals.service import enqueue_goal_intelligence_job, INTEL_STALE, INTEL_PENDING

    goal = await get_goal_by_id(session, goal_id, person.id)
    if goal is None:
        raise AuthError(code="GOAL_NOT_FOUND", message="Goal not found.", status_code=404)

    conv = await get_or_create_person_conversation(session, person, settings=settings)
    await activate_goal(session, goal, conversation_id=conv.id)

    if goal.intelligence_status in (INTEL_STALE, INTEL_PENDING, None):
        await enqueue_goal_intelligence_job(session, goal)

    await session.commit()
    intel = await get_goal_intelligence(session, goal.id)
    return JSONResponse(
        content=success(
            {
                "goal": _serialize_goal(goal, intel),
                "intelligenceEnqueued": goal.intelligence_status in (INTEL_STALE, INTEL_PENDING),
            }
        )
    )
