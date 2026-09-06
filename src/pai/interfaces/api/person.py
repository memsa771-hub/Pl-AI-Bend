import uuid as uuid_mod
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from pai.platform.security.auth.provider import AuthProvider
from pai.interfaces.api.dependencies import (
    get_auth_provider,
    get_db,
    get_validated_access_token,
    resolve_person_from_token,
)
from pai.domains.student.person.service import (
    PersonBootstrapService,
    update_person_profile,
)
from pai.domains.student.person.typed_resources import (
    MODELS,
    create_resource,
    delete_resource,
    list_resources,
    update_resource,
)
from pai.domains.student.normalization.phone import normalize_phone
from pai.interfaces.api.schemas import success

router = APIRouter(prefix="/api/v1/person", tags=["person"])


class PersonProfilePatch(BaseModel):
    fullName: str | None = None
    preferredName: str | None = None
    phone: str | None = None
    version: int = Field(..., ge=1)

    @field_validator("phone")
    @classmethod
    def phone_e164(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_phone(value)


class EducationCreate(BaseModel):
    institution: str
    degree: str | None = None
    major: str | None = None
    graduationYear: int | None = None
    status: str | None = None


class EducationPatch(BaseModel):
    institution: str | None = None
    degree: str | None = None
    major: str | None = None
    graduationYear: int | None = None
    status: str | None = None


@router.post("/bootstrap")
async def bootstrap_person(
    session: Annotated[AsyncSession, Depends(get_db)],
    access_token: Annotated[str, Depends(get_validated_access_token)],
    provider: Annotated[AuthProvider, Depends(get_auth_provider)],
) -> JSONResponse:
    user = await provider.get_user(access_token)
    data = await PersonBootstrapService().bootstrap(session, user)
    return JSONResponse(content=success(data))


@router.get("/me")
async def get_person_me(
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
) -> JSONResponse:
    return JSONResponse(
        content=success(
            {
                "id": str(person.id),
                "email": person.email,
                "emailVerified": person.email_verified,
                "fullName": person.full_name,
                "preferredName": person.preferred_name,
                "phone": person.phone,
                "accountStatus": person.account_status,
                "version": person.version,
                "onboardingCompleted": person.onboarding_completed_at is not None,
                "onboardingCompletedAt": (
                    person.onboarding_completed_at.isoformat()
                    if person.onboarding_completed_at
                    else None
                ),
                "onboardingPath": person.onboarding_path,
            }
        )
    )


@router.get("/journey")
async def get_person_journey(
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
    limit: int = Query(20, ge=1, le=50),
) -> JSONResponse:
    from pai.domains.goals.service import get_active_goal, goal_to_public, list_goals
    from pai.domains.journey.service import event_to_public, list_recent_events

    current = await get_active_goal(session, person.id)
    others = [g for g in await list_goals(session, person.id, include_archived=True) if current is None or g.id != current.id]
    events = await list_recent_events(session, person.id, limit=limit)
    return JSONResponse(
        content=success(
            {
                "currentGoal": goal_to_public(current),
                "previousGoal": goal_to_public(others[0]) if others else None,
                "goalHistory": [goal_to_public(row) for row in others],
                "events": [event_to_public(row) for row in events],
            }
        )
    )


@router.patch("/me")
async def patch_person_me(
    body: PersonProfilePatch,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
) -> JSONResponse:
    updated = await update_person_profile(
        session,
        person,
        expected_version=body.version,
        updates={
            "full_name": body.fullName,
            "preferred_name": body.preferredName,
            "phone": body.phone,
        },
    )
    return JSONResponse(
        content=success(
            {
                "id": str(updated.id),
                "fullName": updated.full_name,
                "preferredName": updated.preferred_name,
                "phone": updated.phone,
                "version": updated.version,
            }
        )
    )


def _resource_router(path: str, model_key: str, create_schema: type[BaseModel], patch_schema: type[BaseModel]):
    model = MODELS[model_key]

    def register(Create: type[BaseModel], Patch: type[BaseModel]) -> None:
        @router.get(f"/{path}")
        async def list_items(
            session: Annotated[AsyncSession, Depends(get_db)],
            person=Depends(resolve_person_from_token),
            limit: int = Query(50, ge=1, le=100),
            offset: int = Query(0, ge=0),
        ):
            rows = await list_resources(session, model, person.id, limit=limit, offset=offset)
            return JSONResponse(content=success({"items": [_row_dict(r) for r in rows]}))

        @router.post(f"/{path}", status_code=201)
        async def create_item(
            body: Create,
            session: Annotated[AsyncSession, Depends(get_db)],
            person=Depends(resolve_person_from_token),
        ):
            row = await create_resource(
                session, model, person, _camel_to_snake(body.model_dump(exclude_none=True))
            )
            return JSONResponse(status_code=201, content=success({"item": _row_dict(row)}))

        @router.patch(f"/{path}/{{item_id}}")
        async def patch_item(
            item_id: str,
            body: Patch,
            session: Annotated[AsyncSession, Depends(get_db)],
            person=Depends(resolve_person_from_token),
        ):
            row = await update_resource(
                session,
                model,
                person,
                uuid_mod.UUID(item_id),
                _camel_to_snake(body.model_dump(exclude_none=True)),
            )
            return JSONResponse(content=success({"item": _row_dict(row)}))

        @router.delete(f"/{path}/{{item_id}}")
        async def remove_item(
            item_id: str,
            session: Annotated[AsyncSession, Depends(get_db)],
            person=Depends(resolve_person_from_token),
        ):
            await delete_resource(session, model, person, uuid_mod.UUID(item_id))
            return JSONResponse(content=success({"message": "Deleted."}))

    register(create_schema, patch_schema)


def _row_dict(row: Any) -> dict[str, Any]:
    data = {c.name: getattr(row, c.name) for c in row.__table__.columns}
    for k, v in list(data.items()):
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
        elif hasattr(v, "hex"):
            data[k] = str(v)
    return data


def _camel_to_snake(data: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in k).lstrip("_")
        out[snake] = v
    return out


_resource_router("educations", "educations", EducationCreate, EducationPatch)

# Additional typed routes use simplified schemas inline
class WorkCreate(BaseModel):
    organization: str
    title: str
    employmentType: str | None = None
    isCurrent: bool = False
    description: str | None = None


class WorkPatch(BaseModel):
    organization: str | None = None
    title: str | None = None
    employmentType: str | None = None
    isCurrent: bool | None = None
    description: str | None = None


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    role: str | None = None
    url: str | None = None


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    role: str | None = None
    url: str | None = None


class SkillCreate(BaseModel):
    name: str
    proficiency: str | None = None
    yearsExperience: float | None = None


class SkillPatch(BaseModel):
    name: str | None = None
    proficiency: str | None = None
    yearsExperience: float | None = None


class CertCreate(BaseModel):
    name: str
    issuer: str | None = None
    credentialUrl: str | None = None


class CertPatch(BaseModel):
    name: str | None = None
    issuer: str | None = None
    credentialUrl: str | None = None


class GoalCreate(BaseModel):
    goalType: str
    title: str
    description: str | None = None
    status: str | None = None
    priority: str | None = None


class GoalPatch(BaseModel):
    goalType: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None


_resource_router("work-experiences", "work-experiences", WorkCreate, WorkPatch)
_resource_router("projects", "projects", ProjectCreate, ProjectPatch)
_resource_router("skills", "skills", SkillCreate, SkillPatch)
_resource_router("certifications", "certifications", CertCreate, CertPatch)
_resource_router("goals", "goals", GoalCreate, GoalPatch)
