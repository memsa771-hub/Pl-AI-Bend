from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.kernel.errors import (
    ConsentRequiredError,
    FieldNotEditableError,
    UnknownFieldError,
    VersionConflictError,
)
from pai.domains.student.person.models import (
    Person,
    PersonConsent,
    PersonVault,
    VaultEvidence,
    VaultHistory,
    VaultValue,
)
from pai.domains.student.vault.catalog import CATALOG_VERSION, GUIDANCE_SCOPES, CatalogField, get_catalog_field
from pai.domains.student.vault.completion import apply_completion_to_vault
from pai.domains.student.vault.security import SensitiveValueCodec, mask_value


class VaultService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._codec = SensitiveValueCodec(self._settings.vault_encryption_key)

    async def get_sparse_fields(
        self,
        session: AsyncSession,
        person: Person,
        *,
        include_sensitive: bool = False,
    ) -> dict[str, Any]:
        """Active vault_value map only — no completion scan or typed counts."""
        vault = person.vault
        if vault is None:
            return {}
        consents = await self._consent_map(session, person.id)
        return await self._load_sparse_fields(session, vault, consents, include_sensitive)

    async def get_unified_vault(
        self,
        session: AsyncSession,
        person: Person,
        *,
        include_sensitive: bool = False,
        typed_records: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        vault = person.vault
        if vault is None:
            return {"fields": {}, "typed": {}, "completion": {}}
        from pai.domains.student.vault.completion import compute_completion, compute_completion_from_snapshot

        consents = await self._consent_map(session, person.id)
        sparse = await self._load_sparse_fields(session, vault, consents, include_sensitive)
        if typed_records is not None:
            typed_present = {
                "educations": bool(typed_records.get("educations")),
                "work_experiences": bool(typed_records.get("workExperiences")),
                "projects": bool(typed_records.get("projects")),
                "skills": bool(typed_records.get("skills")),
                "certifications": bool(typed_records.get("certifications")),
                "goals": bool(typed_records.get("goals")),
            }
            snapshot = {
                "active_keys": set(sparse.keys()),
                "typed_present": typed_present,
            }
            completion = compute_completion_from_snapshot(person, vault, snapshot)
            completion.pop("_present_map", None)
            typed = typed_records.get("counts") or {
                "educations": str(len(typed_records.get("educations") or [])),
                "workExperiences": str(len(typed_records.get("workExperiences") or [])),
                "projects": str(len(typed_records.get("projects") or [])),
                "skills": str(len(typed_records.get("skills") or [])),
                "certifications": str(len(typed_records.get("certifications") or [])),
                "goals": str(len(typed_records.get("goals") or [])),
            }
        else:
            from pai.domains.student.vault.completion import load_presence_snapshot

            snapshot = await load_presence_snapshot(session, person, vault)
            snapshot["active_keys"] = set(sparse.keys()) or snapshot.get("active_keys") or set()
            typed = snapshot.get("typed_resources") or await self._load_typed_summary(
                session, person.id
            )
            completion = await compute_completion(session, person, vault, snapshot=snapshot)
        return {
            "vaultId": str(vault.id),
            "catalogVersion": vault.catalog_version,
            "applicableScopes": vault.applicable_scopes,
            "sparseFields": sparse,
            "typedResources": typed,
            "completion": completion,
            "version": vault.version,
        }

    async def set_field(
        self,
        session: AsyncSession,
        person: Person,
        field_key: str,
        value: Any,
        *,
        expected_version: int | None,
    ) -> dict[str, Any]:
        field = get_catalog_field(field_key)
        if field is None:
            raise UnknownFieldError()
        if field.derived or not field.editable:
            raise FieldNotEditableError()
        if field.storage != "vault_value":
            raise FieldNotEditableError("Update the typed profile resource for this field.")
        if field.consent_category and not await self._has_consent(session, person.id, field.consent_category):
            raise ConsentRequiredError()

        vault = person.vault
        if vault is None:
            raise UnknownFieldError("Vault not initialized.")
        if expected_version is not None and vault.version != expected_version:
            raise VersionConflictError()

        async with session.begin():
            result = await session.execute(
                select(VaultValue).where(
                    VaultValue.vault_id == vault.id,
                    VaultValue.field_key == field_key,
                    VaultValue.status == "active",
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.status = "superseded"
                old_val = existing.value
            else:
                old_val = None

            row = VaultValue(
                vault_id=vault.id,
                field_key=field_key,
                value=None if field.sensitive else value,
                value_encrypted=self._codec.encrypt_json(value) if field.sensitive else None,
                status="active",
                verification_level="self_reported",
                confidence=1.0,
                supersedes_id=existing.id if existing else None,
            )
            session.add(row)
            await session.flush()
            session.add(
                VaultEvidence(
                    vault_value_id=row.id,
                    source_type="manual",
                    source_reference=str(person.id),
                    confidence=1.0,
                )
            )
            session.add(
                VaultHistory(
                    vault_id=vault.id,
                    field_key=field_key,
                    action="updated" if old_val is not None else "created",
                    old_value=_history_value(field, old_val),
                    new_value=_history_value(field, value),
                    actor_type="person",
                    actor_id=str(person.id),
                )
            )
            await self._maybe_expand_scopes(session, vault, field.applicable_scope)
            await apply_completion_to_vault(session, person, vault)

        return await self.get_field(session, person, field_key, include_sensitive=True)

    async def delete_field(
        self,
        session: AsyncSession,
        person: Person,
        field_key: str,
        *,
        expected_version: int | None,
    ) -> None:
        field = get_catalog_field(field_key)
        if field is None:
            raise UnknownFieldError()
        if field.derived or not field.editable or field.storage != "vault_value":
            raise FieldNotEditableError()
        vault = person.vault
        if vault is None:
            return
        if expected_version is not None and vault.version != expected_version:
            raise VersionConflictError()
        async with session.begin():
            result = await session.execute(
                select(VaultValue).where(
                    VaultValue.vault_id == vault.id,
                    VaultValue.field_key == field_key,
                    VaultValue.status == "active",
                )
            )
            existing = result.scalar_one_or_none()
            if not existing:
                return
            existing.status = "deleted"
            session.add(
                VaultHistory(
                    vault_id=vault.id,
                    field_key=field_key,
                    action="deleted",
                    old_value=_history_value(field, existing.value),
                    new_value=None,
                    actor_type="person",
                    actor_id=str(person.id),
                )
            )
            await apply_completion_to_vault(session, person, vault)

    async def get_field(
        self,
        session: AsyncSession,
        person: Person,
        field_key: str,
        *,
        include_sensitive: bool = False,
    ) -> dict[str, Any]:
        field = get_catalog_field(field_key)
        if field is None:
            raise UnknownFieldError()
        if field.storage == "person" and field.person_column:
            raw = getattr(person, field.person_column)
            hide = (field.sensitive or field_key == "identity.phone") and not include_sensitive
            if hide:
                return {
                    "key": field_key,
                    "value": "***" if raw else None,
                    "masked": bool(raw),
                }
            return {"key": field_key, "value": raw, "masked": False}
        vault = person.vault
        if vault is None:
            return {"key": field_key, "value": None, "masked": False}
        result = await session.execute(
            select(VaultValue).where(
                VaultValue.vault_id == vault.id,
                VaultValue.field_key == field_key,
                VaultValue.status == "active",
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return {"key": field_key, "value": None, "masked": False}
        if field.sensitive and not include_sensitive:
            return {"key": field_key, "value": mask_value(row.value), "masked": True}
        value = row.value
        if row.value_encrypted:
            value = self._codec.decrypt_json(row.value_encrypted)
        return {
            "key": field_key,
            "value": value,
            "verificationLevel": row.verification_level,
            "masked": False,
        }

    async def field_history(
        self, session: AsyncSession, person: Person, field_key: str
    ) -> list[dict[str, Any]]:
        if person.vault is None:
            return []
        result = await session.execute(
            select(VaultHistory)
            .where(VaultHistory.vault_id == person.vault.id, VaultHistory.field_key == field_key)
            .order_by(VaultHistory.created_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "action": r.action,
                "oldValue": r.old_value,
                "newValue": r.new_value,
                "actorType": r.actor_type,
                "createdAt": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    async def _load_sparse_fields(
        self,
        session: AsyncSession,
        vault: PersonVault,
        consents: dict[str, bool],
        include_sensitive: bool,
    ) -> dict[str, Any]:
        result = await session.execute(
            select(VaultValue).where(
                VaultValue.vault_id == vault.id,
                VaultValue.status == "active",
            )
        )
        out: dict[str, Any] = {}
        for row in result.scalars():
            field = get_catalog_field(row.field_key)
            if field is None:
                continue
            if field.sensitive:
                if field.consent_category and not consents.get(field.consent_category):
                    out[row.field_key] = mask_value(None)
                    continue
                if not include_sensitive:
                    out[row.field_key] = mask_value(None)
                    continue
                if row.value_encrypted:
                    out[row.field_key] = self._codec.decrypt_json(row.value_encrypted)
                else:
                    out[row.field_key] = row.value
            else:
                out[row.field_key] = row.value
        return out

    async def _load_typed_summary(self, session: AsyncSession, person_id: uuid.UUID) -> dict[str, str]:
        from sqlalchemy import func

        from pai.domains.goals.models import Goal
        from pai.domains.student.person.models import (
            Certification,
            Education,
            Project,
            Skill,
            WorkExperience,
        )

        # Single round-trip style: sequential counts but shared with completion snapshot
        # when callers use load_presence_snapshot — keep API shape stable.
        models = [
            ("educations", Education),
            ("workExperiences", WorkExperience),
            ("projects", Project),
            ("skills", Skill),
            ("certifications", Certification),
            ("goals", Goal),
        ]
        summary: dict[str, str] = {}
        for name, model in models:
            count = await session.scalar(
                select(func.count()).select_from(model).where(model.person_id == person_id)
            )
            summary[name] = str(count or 0)
        return summary

    async def upsert_sparse_field(
        self,
        session: AsyncSession,
        person: Person,
        field_key: str,
        value: Any,
        *,
        source_type: str = "onboarding",
        actor_type: str = "person",
        skip_consent_check: bool = False,
    ) -> None:
        await self.upsert_sparse_fields(
            session,
            person,
            [(field_key, value)],
            source_type=source_type,
            actor_type=actor_type,
            skip_consent_check=skip_consent_check,
        )

    async def upsert_sparse_fields(
        self,
        session: AsyncSession,
        person: Person,
        items: list[tuple[str, Any]],
        *,
        source_type: str = "onboarding",
        actor_type: str = "person",
        skip_consent_check: bool = False,
    ) -> None:
        """Write many vault_value fields in one select + one flush, then evidence rows."""
        if not items:
            return
        vault = person.vault
        if vault is None:
            raise UnknownFieldError("Vault not initialized.")

        prepared: list[tuple[Any, Any]] = []
        for field_key, value in items:
            field = get_catalog_field(field_key)
            if (
                field is None
                or field.derived
                or not field.editable
                or field.storage != "vault_value"
            ):
                continue
            prepared.append((field, value))
        if not prepared:
            return

        if not skip_consent_check:
            needed = {field.consent_category for field, _ in prepared if field.consent_category}
            if needed:
                consents = await self._consent_map(session, person.id)
                if any(not consents.get(category) for category in needed):
                    raise ConsentRequiredError()

        keys = [field.key for field, _ in prepared]
        result = await session.execute(
            select(VaultValue).where(
                VaultValue.vault_id == vault.id,
                VaultValue.field_key.in_(keys),
                VaultValue.status == "active",
            )
        )
        existing_by_key: dict[str, VaultValue] = {}
        for row in result.scalars():
            existing_by_key.setdefault(row.field_key, row)

        pending: list[tuple[VaultValue, Any, Any, Any]] = []
        scopes = list(vault.applicable_scopes or [])
        for field, value in prepared:
            existing = existing_by_key.get(field.key)
            if existing:
                existing.status = "superseded"
                old_val = existing.value
            else:
                old_val = None
            row = VaultValue(
                id=uuid.uuid4(),
                vault_id=vault.id,
                field_key=field.key,
                value=None if field.sensitive else value,
                value_encrypted=self._codec.encrypt_json(value) if field.sensitive else None,
                status="active",
                verification_level="self_reported",
                confidence=1.0,
                supersedes_id=existing.id if existing else None,
            )
            session.add(row)
            pending.append((row, field, value, old_val))
            existing_by_key[field.key] = row
            if field.applicable_scope not in scopes:
                scopes.append(field.applicable_scope)
        if scopes != list(vault.applicable_scopes or []):
            vault.applicable_scopes = scopes
        # Parent rows must exist before vault_evidence FK inserts.
        await session.flush()
        for row, field, value, old_val in pending:
            session.add(
                VaultEvidence(
                    vault_value_id=row.id,
                    source_type=source_type,
                    source_reference=str(person.id),
                    confidence=1.0,
                )
            )
            session.add(
                VaultHistory(
                    vault_id=vault.id,
                    field_key=row.field_key,
                    action="updated" if old_val is not None else "created",
                    old_value=_history_value(field, old_val),
                    new_value=_history_value(field, value),
                    actor_type=actor_type,
                    actor_id=str(person.id),
                )
            )

    async def ensure_consent(
        self, session: AsyncSession, person_id: uuid.UUID, category: str
    ) -> None:
        await self.ensure_consents(session, person_id, [category])

    async def ensure_consents(
        self, session: AsyncSession, person_id: uuid.UUID, categories: Iterable[str]
    ) -> None:
        wanted = [category for category in dict.fromkeys(categories) if category]
        if not wanted:
            return
        result = await session.execute(
            select(PersonConsent).where(
                PersonConsent.person_id == person_id,
                PersonConsent.category.in_(wanted),
            )
        )
        have = {row.category: row for row in result.scalars()}
        now = datetime.now(UTC)
        for category in wanted:
            row = have.get(category)
            if row is None:
                session.add(
                    PersonConsent(
                        person_id=person_id,
                        category=category,
                        granted=True,
                        granted_at=now,
                    )
                )
                continue
            row.granted = True
            row.granted_at = row.granted_at or now
            row.revoked_at = None

    async def _consent_map(self, session: AsyncSession, person_id: uuid.UUID) -> dict[str, bool]:
        result = await session.execute(
            select(PersonConsent).where(PersonConsent.person_id == person_id)
        )
        return {row.category: row.granted for row in result.scalars()}

    async def _has_consent(self, session: AsyncSession, person_id: uuid.UUID, category: str) -> bool:
        result = await session.execute(
            select(PersonConsent).where(
                PersonConsent.person_id == person_id,
                PersonConsent.category == category,
                PersonConsent.granted.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    async def _maybe_expand_scopes(
        self, session: AsyncSession, vault: PersonVault, scope: str
    ) -> None:
        scopes = list(vault.applicable_scopes or [])
        if scope not in scopes:
            scopes.append(scope)
            vault.applicable_scopes = scopes


def grow_vault_schema(vault: PersonVault) -> bool:
    """Attach new catalog version + guidance scopes. New fields stay empty until filled."""
    dirty = False
    if vault.catalog_version != CATALOG_VERSION:
        vault.catalog_version = CATALOG_VERSION
        dirty = True
    scopes = list(vault.applicable_scopes or [])
    extra = [scope for scope in GUIDANCE_SCOPES if scope not in scopes]
    if extra:
        vault.applicable_scopes = scopes + extra
        dirty = True
    return dirty


def _history_value(field: CatalogField, value: Any) -> Any:
    if field.sensitive:
        return "***" if value not in (None, "") else None
    return value


async def expand_scope_for_person(
    session: AsyncSession, person: Person, scope: str
) -> None:
    if person.vault is None:
        return
    scopes = list(person.vault.applicable_scopes or [])
    if scope not in scopes:
        person.vault.applicable_scopes = scopes + [scope]
        await apply_completion_to_vault(session, person, person.vault)
