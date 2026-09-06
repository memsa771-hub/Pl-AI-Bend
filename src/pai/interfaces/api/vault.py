from __future__ import annotations

from typing import Annotated, Any

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
