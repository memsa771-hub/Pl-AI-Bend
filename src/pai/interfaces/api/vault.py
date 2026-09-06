from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal
from sqlalchemy import select
from pai.domains.student.person.models import VaultValue

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from pai.interfaces.api.dependencies import get_db, resolve_person_from_token
from pai.interfaces.api.schemas import success
from pai.domains.student.vault.catalog import CATALOG_VERSION, VAULT_CATALOG
from pai.domains.student.vault.completion import build_vault_status
from pai.domains.student.vault.service import VaultService

router = APIRouter(prefix="/api/v1/vault", tags=["vault"])


class VaultFieldPatch(BaseModel):
    value: Any
    version: int | None = Field(default=None, ge=1)


@router.get(
    "",
    summary="Get Person Vault",
)
async def get_vault(
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
    includeSensitive: bool = Query(False, alias="includeSensitive"),
) -> JSONResponse:
    data = await build_vault_status(
        session, person, include_sensitive=includeSensitive
    )
    return JSONResponse(content=success(data))


@router.get("/catalog")
async def get_catalog(
    _person=Depends(resolve_person_from_token),
) -> JSONResponse:
    fields = [
        {
            "key": f.key,
            "section": f.section,
            "priority": f.priority,
            "sensitive": f.sensitive,
            "derived": f.derived,
            "storage": f.storage,
            "applicableScope": f.applicable_scope,
            "valueType": f.value_type,
            "editable": f.editable,
            "repeatable": f.repeatable,
        }
        for f in VAULT_CATALOG.values()
    ]
    return JSONResponse(content=success({"catalogVersion": CATALOG_VERSION, "fields": fields}))


@router.get("/fields/{field_key}")
async def get_field(
    field_key: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
    includeSensitive: bool = Query(False, alias="includeSensitive"),
) -> JSONResponse:
    data = await VaultService().get_field(
        session, person, field_key, include_sensitive=includeSensitive
    )
    return JSONResponse(content=success(data))


@router.patch("/fields/{field_key}")
async def patch_field(
    field_key: str,
    body: VaultFieldPatch,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
) -> JSONResponse:
    data = await VaultService().set_field(
        session,
        person,
        field_key,
        body.value,
        expected_version=body.version,
    )
    return JSONResponse(content=success(data))


@router.delete("/fields/{field_key}")
async def delete_field(
    field_key: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
    version: int | None = Query(None, ge=1),
) -> JSONResponse:
    await VaultService().delete_field(
        session, person, field_key, expected_version=version
    )
    return JSONResponse(content=success({"message": "Field removed."}))


@router.get("/fields/{field_key}/history")
async def field_history(
    field_key: str,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
) -> JSONResponse:
    rows = await VaultService().field_history(session, person, field_key)
    return JSONResponse(content=success({"history": rows}))


class ProposalResolution(BaseModel):
    decision: Literal["accept", "reject"]
    version: int = Field(ge=1)
    vaultVersion: int = Field(ge=1)


@router.get("/proposals")
async def list_proposals(
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
) -> JSONResponse:
    rows = (await session.execute(select(VaultValue).where(
        VaultValue.vault_id == person.vault.id,
        VaultValue.status == "pending_confirmation",
    ).order_by(VaultValue.created_at))).scalars().all()
    codec = VaultService()._codec
    proposals = []
    for row in rows:
        field = VAULT_CATALOG.get(row.field_key)
        if field is None:
            continue
        if field.consent_category and not await VaultService()._has_consent(
            session, person.id, field.consent_category
        ):
            continue
        proposals.append({"id": str(row.id), "fieldKey": row.field_key,
            "value": codec.decrypt_json(row.value_encrypted) if field.sensitive and row.value_encrypted else row.value,
            "version": row.version, "confidence": row.confidence})
    return JSONResponse(content=success({"proposals": proposals, "vaultVersion": person.vault.version}))


@router.post("/proposals/{proposal_id}/resolve")
async def resolve_proposal(
    proposal_id: uuid.UUID,
    body: ProposalResolution,
    session: Annotated[AsyncSession, Depends(get_db)],
    person=Depends(resolve_person_from_token),
) -> JSONResponse:
    from pai.domains.student.person.write_lock import lock_person
    from pai.kernel.contracts.schemas import VaultCandidate
    from pai.kernel.evidence.vault_apply import process_candidates
    from pai.kernel.errors import AuthError, VersionConflictError, ConsentRequiredError

    await lock_person(session, person.id)
    row = (await session.execute(select(VaultValue).where(
        VaultValue.id == proposal_id, VaultValue.vault_id == person.vault.id
    ).with_for_update())).scalar_one_or_none()
    if row is None:
        raise AuthError("PROPOSAL_NOT_FOUND", "Proposal not found.", 404)
    target = "confirmed" if body.decision == "accept" else "rejected"
    if row.status == target:
        return JSONResponse(content=success({"id": str(row.id), "status": target}))
    if row.status != "pending_confirmation" or row.version != body.version:
        raise VersionConflictError()
    field = VAULT_CATALOG.get(row.field_key)
    if field is None:
        raise AuthError("UNKNOWN_FIELD", "Unknown proposal field.", 422)
    service = VaultService()
    if field.consent_category and not await service._has_consent(session, person.id, field.consent_category):
        raise ConsentRequiredError()
    if body.decision == "accept":
        await session.refresh(person.vault)
        if person.vault.version != body.vaultVersion:
            raise VersionConflictError()
        if row.supersedes_id:
            previous = await session.get(VaultValue, row.supersedes_id)
            if previous is None or previous.status != "active":
                raise VersionConflictError("The current value changed; review a fresh proposal.")
        value = service._codec.decrypt_json(row.value_encrypted) if field.sensitive and row.value_encrypted else row.value
        outcomes, _ = await process_candidates(session, person, [VaultCandidate(
            field_key=row.field_key, value=value, confidence=1.0, source_type="manual",
            source_reference=str(row.id), evidence_text="Student confirmed this proposal.",
            attributed_to="self", assertion_status="explicit", temporal_status="current",
        )], already_reconciled=True)
        if not outcomes or any(item.status not in ("accepted", "reinforced", "updated") for item in outcomes):
            await session.rollback()
            raise AuthError("PROPOSAL_NEEDS_DETAIL", "This proposal needs more detail before it can be accepted.", 422)
    row.status = target
    row.version += 1
    await session.commit()
    return JSONResponse(content=success({"id": str(row.id), "status": target}))
