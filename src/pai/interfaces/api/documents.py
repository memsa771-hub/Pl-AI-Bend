from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.domains.documents.models import Document, DocumentJob
from pai.domains.documents.service import (
    enqueue_reprocess,
    get_document_owned,
    list_document_candidates,
    list_document_storage_paths,
    list_documents,
    mark_document_deleted,
    review_document_candidates,
)
from pai.intelligences.documents.config import policy, taxonomy as load_taxonomy
from pai.intelligences.documents.evidence.attention import attention_state, journey_criticality
from pai.intelligences.documents.ingest import create_document_upload
from pai.intelligences.documents.verification.service import (
    close_open_cases_for_fields,
    list_open_cases,
    public_case,
    resolve_case,
)
from pai.interfaces.api.dependencies import get_db, require_onboarding_complete
from pai.interfaces.api.schemas import success
from pai.kernel.gates import accept_vault_candidates
from pai.platform.storage.supabase import SupabaseStorageProvider

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


class DocumentReviewRequest(BaseModel):
    acceptCandidateIds: list[uuid.UUID] = Field(default_factory=list)
    rejectCandidateIds: list[uuid.UUID] = Field(default_factory=list)


class VerificationResolveRequest(BaseModel):
    resolutionType: str
    notes: str | None = None


def _storage(settings: Settings) -> SupabaseStorageProvider:
    return SupabaseStorageProvider(settings)


def _public_document(doc: Document, *, open_cases: int = 0) -> dict:
    attention = attention_state(doc, open_cases=open_cases)
    return {
        "id": str(doc.id),
        "title": doc.title or doc.original_filename,
        "filename": doc.original_filename,
        "documentType": doc.document_type or "other",
        "category": doc.category,
        "sourceType": doc.source_type,
        "createdBy": doc.created_by,
        "processingStatus": doc.status,
        "status": doc.status,
        "verificationStatus": doc.verification_status,
        "lifecycleStatus": doc.lifecycle_status,
        "trustLevel": doc.trust_level,
        "identityStatus": doc.identity_status,
        "authenticityStatus": doc.authenticity_status,
        "baseCriticality": doc.base_criticality,
        "journeyCriticality": journey_criticality(doc, attention=attention),
        "attentionState": attention,
        "evidenceEligible": doc.evidence_eligible,
        "sizeBytes": doc.size_bytes,
        "mimeType": doc.mime_type,
        "currentVersionId": str(doc.current_version_id) if doc.current_version_id else None,
        "vaultExtractionPolicy": doc.vault_extraction_policy,
        "createdAt": doc.created_at.isoformat() if doc.created_at else None,
        "updatedAt": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.post("")
async def upload_document(
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
    file: UploadFile = File(...),
    document_type: str | None = Form(default=None, alias="documentType"),
    source_type: str | None = Form(default="document_vault", alias="sourceType"),
) -> JSONResponse:
    allowed_sources = set(load_taxonomy()["source_types"]) - {"ai_generated"}
    if source_type not in allowed_sources:
        source_type = "document_vault"
    data = await file.read()
    content_type = file.content_type or "application/octet-stream"
    storage = _storage(settings)
    try:
        doc = await create_document_upload(
            session,
            settings,
            person,
            filename=file.filename or "upload.bin",
            content_type=content_type,
            data=data,
            storage=storage,
            source_type=source_type or "document_vault",
            document_type=document_type,
            created_by="student",
        )
    finally:
        await storage.aclose()
    return JSONResponse(status_code=202, content=success(_public_document(doc)))


@router.get("/taxonomy")
async def document_taxonomy(person=Depends(require_onboarding_complete)) -> JSONResponse:
    _ = person
    tax = load_taxonomy()
    return JSONResponse(content=success({"categories": tax["categories"], "types": tax["types"]}))


@router.get("/verification-cases")
async def verification_cases_api(
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    rows = await list_open_cases(session, person.id)
    return JSONResponse(content=success({"items": [public_case(row) for row in rows]}))


@router.post("/verification-cases/{case_id}/resolve")
async def resolve_verification_case_api(
    case_id: uuid.UUID,
    body: VerificationResolveRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    row = await resolve_case(
        session, person, case_id, resolution_type=body.resolutionType, notes=body.notes
    )
    return JSONResponse(content=success(public_case(row)))


@router.get("")
async def list_documents_api(
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    items = await list_documents(session, person.id)
    return JSONResponse(content=success({"items": [_public_document(d) for d in items]}))


@router.get("/{document_id}")
async def get_document(
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    doc = await get_document_owned(session, person.id, document_id)
    storage = _storage(settings)
    try:
        signed = await storage.create_signed_download_url(
            doc.storage_path, person_id=person.id, expires_seconds=900
        )
    finally:
        await storage.aclose()
    payload = _public_document(doc)
    payload["downloadUrl"] = signed
    return JSONResponse(content=success(payload))


@router.delete("/{document_id}")
async def delete_document_api(
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    storage = _storage(settings)
    try:
        paths = await list_document_storage_paths(session, person, document_id)
        for path in paths:
            await storage.delete_object(path)
        await mark_document_deleted(session, person, document_id)
        await session.commit()
    finally:
        await storage.aclose()
    return JSONResponse(content=success({"message": "Document deleted."}))


@router.get("/{document_id}/status")
async def document_status(
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    doc = await get_document_owned(session, person.id, document_id)
    job = await session.scalar(
        select(DocumentJob)
        .where(DocumentJob.document_id == doc.id)
        .order_by(DocumentJob.created_at.desc())
        .limit(1)
    )
    return JSONResponse(
        content=success(
            {
                "status": doc.status,
                "processingStatus": doc.status,
                "documentType": doc.document_type,
                "category": doc.category,
                "sourceType": doc.source_type,
                "verificationStatus": doc.verification_status,
                "lifecycleStatus": doc.lifecycle_status,
                "identityStatus": doc.identity_status,
                "attentionState": _public_document(doc)["attentionState"],
                "vaultExtractionPolicy": doc.vault_extraction_policy,
                "currentStage": job.current_stage if job else None,
                "jobStatus": job.status if job else None,
                "lastError": job.last_error if job else None,
            }
        )
    )


@router.get("/{document_id}/candidates")
async def document_candidates(
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    items = await list_document_candidates(session, person, document_id)
    return JSONResponse(content=success({"candidates": items}))


@router.post("/{document_id}/review")
async def review_document(
    document_id: uuid.UUID,
    body: DocumentReviewRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    applied, to_apply = await review_document_candidates(
        session,
        person,
        document_id,
        accept_ids=body.acceptCandidateIds,
        reject_ids=body.rejectCandidateIds,
    )
    if to_apply:
        await accept_vault_candidates(
            session,
            person,
            to_apply,
            from_document=True,
            already_reconciled=True,
            apply_order=list(policy().get("apply_order") or []),
        )
    if applied:
        await close_open_cases_for_fields(
            session,
            person_id=person.id,
            document_id=document_id,
            field_keys=applied,
        )
    await session.commit()
    return JSONResponse(content=success({"message": "Review applied."}))


@router.post("/{document_id}/reprocess")
async def reprocess_document(
    document_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(require_onboarding_complete),
) -> JSONResponse:
    await enqueue_reprocess(session, person.id, document_id)
    return JSONResponse(content=success({"message": "Reprocess queued."}))
