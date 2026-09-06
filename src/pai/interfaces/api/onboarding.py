from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.interfaces.api.dependencies import get_db, resolve_person_from_token
from pai.platform.llm.gateway import LLMGateway
from pai.workflows.onboarding.contracts import OnboardingSubmit
from pai.workflows.onboarding.service import OnboardingService
from pai.domains.student.person.models import Person
from pai.interfaces.api.schemas import ApiErrorResponse, success
from pai.platform.storage.supabase import SupabaseStorageProvider

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])

_ONBOARDING_ERRORS = {
    400: {"model": ApiErrorResponse},
    401: {"model": ApiErrorResponse},
    403: {"model": ApiErrorResponse},
    422: {"model": ApiErrorResponse},
}


def _svc(settings: Settings) -> OnboardingService:
    return OnboardingService(settings)


@router.get(
    "",
    responses=_ONBOARDING_ERRORS,
    summary="Get onboarding status",
)
async def get_onboarding(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person: Annotated[Person, Depends(resolve_person_from_token)],
) -> JSONResponse:
    data = await _svc(settings).status(session, person)
    return JSONResponse(content=success(data))


@router.post(
    "",
    responses=_ONBOARDING_ERRORS,
    summary="Submit starting profile",
)
async def submit_onboarding(
    body: OnboardingSubmit,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person: Annotated[Person, Depends(resolve_person_from_token)],
) -> JSONResponse:
    data = await _svc(settings).submit(session, person, body)
    return JSONResponse(content=success(data))


@router.post(
    "/cv",
    responses=_ONBOARDING_ERRORS,
    summary="Complete onboarding with a CV",
)
async def upload_onboarding_cv(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person: Annotated[Person, Depends(resolve_person_from_token)],
    file: UploadFile = File(...),
) -> JSONResponse:
    data = await file.read()
    storage = SupabaseStorageProvider(settings)
    gateway: LLMGateway = getattr(request.app.state, "llm_gateway", None) or LLMGateway(settings)
    try:
        result = await _svc(settings).ingest_cv(
            session,
            person,
            filename=file.filename or "cv.pdf",
            content_type=file.content_type or "application/pdf",
            data=data,
            storage=storage,
            gateway=gateway,
        )
    finally:
        await storage.aclose()
    return JSONResponse(content=success(result))
