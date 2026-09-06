from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pai.config import Settings, get_settings
from pai.kernel.errors import (
    EmailNotVerifiedError,
    PersonNotFoundError,
    VersionConflictError,
)
from pai.platform.security.auth.provider import ProviderUser
from pai.domains.goals.models import Goal
from pai.domains.student.person.models import (
    Certification,
    Education,
    Person,
    PersonConsent,
    PersonVault,
    Project,
    Skill,
    VaultEvidence,
    VaultHistory,
    VaultValue,
    WorkExperience,
)
from pai.domains.student.vault.catalog import AUTH_PROVIDER_NAME, CATALOG_VERSION, GUIDANCE_SCOPES
from pai.domains.student.vault.completion import apply_completion_to_vault
from pai.domains.student.vault.security import SensitiveValueCodec
from pai.domains.student.vault.service import grow_vault_schema

logger = logging.getLogger(__name__)


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


class PersonBootstrapService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._codec = SensitiveValueCodec(self._settings.vault_encryption_key)

    async def bootstrap(
        self,
        session: AsyncSession,
        provider_user: ProviderUser,
    ) -> dict[str, Any]:
        if not provider_user.email_verified:
            raise EmailNotVerifiedError()

        email = normalize_email(provider_user.email)
        external_id = str(provider_user.id)

        try:
            async with session.begin():
                person = await self._get_person_for_update(
                    session, AUTH_PROVIDER_NAME, external_id
                )
                if person is None:
                    person = Person(
                        auth_provider=AUTH_PROVIDER_NAME,
                        external_auth_id=external_id,
                        email=email,
                        email_verified=True,
                        full_name=provider_user.display_name,
                        phone=provider_user.phone,
                        account_status="active",
                        onboarding_completed_at=None,
                    )
                    session.add(person)
                    await session.flush()

                person.email = email
                person.email_verified = True
                if provider_user.display_name and not person.full_name:
                    person.full_name = provider_user.display_name
                if provider_user.phone and not person.phone:
                    person.phone = provider_user.phone
                person.account_status = "active"

                vault = await self._get_vault_for_update(session, person.id)
                if vault is None:
                    vault = PersonVault(
                        person_id=person.id,
                        catalog_version=CATALOG_VERSION,
                        applicable_scopes=list(GUIDANCE_SCOPES),
                    )
                    session.add(vault)
                    await session.flush()

                await self._import_auth_fields(session, person, vault, provider_user)
                await session.refresh(person, attribute_names=["vault"])
                person.vault = vault
                completion = await apply_completion_to_vault(session, person, vault)
        except IntegrityError:
            await session.rollback()
            async with session.begin():
                person = await self._get_person_for_update(
                    session, AUTH_PROVIDER_NAME, external_id
                )
                if person is None:
                    raise
                vault = await self._get_vault_for_update(session, person.id)
                if vault is None:
                    raise
                await session.refresh(person, attribute_names=["vault"])
                person.vault = vault
                completion = await apply_completion_to_vault(session, person, vault)

        return {
            "person": self._person_dict(person),
            "vault": self._vault_summary(person.vault, completion),
        }

    async def ensure_person(self, session: AsyncSession, provider_user: ProviderUser) -> Person:
        """Create the Person Vault on first verified auth; skip heavy work on later logins."""
        if not provider_user.email_verified:
            raise EmailNotVerifiedError()
        external_id = str(provider_user.id)
        person = await self._find_person(session, AUTH_PROVIDER_NAME, external_id)
        if person is not None:
            dirty = self._sync_identity(person, provider_user)
            vault = person.vault
            # Login is auth + person flags. Catalog grow is a cheap column write;
            # do not scan typed tables / recompute completion on the login path.
            if vault is not None and grow_vault_schema(vault):
                dirty = True
            if dirty:
                await session.commit()
            return person
        if session.in_transaction():
            await session.rollback()
        await self.bootstrap(session, provider_user)
        return await get_person_by_auth(session, external_id)

    def _sync_identity(self, person: Person, provider_user: ProviderUser) -> bool:
        dirty = False
        email = normalize_email(provider_user.email)
        if person.email != email:
            person.email = email
            dirty = True
        if not person.email_verified:
            person.email_verified = True
            dirty = True
        if provider_user.display_name and not person.full_name:
            person.full_name = provider_user.display_name
            dirty = True
        if provider_user.phone and not person.phone:
            person.phone = provider_user.phone
            dirty = True
        if person.account_status != "active":
            person.account_status = "active"
            dirty = True
        return dirty

    async def _find_person(
        self, session: AsyncSession, provider: str, external_id: str
    ) -> Person | None:
        stmt = (
            select(Person)
            .where(
                Person.auth_provider == provider,
                Person.external_auth_id == external_id,
                Person.deleted_at.is_(None),
            )
            .options(selectinload(Person.vault))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_person_for_update(
        self, session: AsyncSession, provider: str, external_id: str
    ) -> Person | None:
        stmt = (
            select(Person)
            .where(
                Person.auth_provider == provider,
                Person.external_auth_id == external_id,
                Person.deleted_at.is_(None),
            )
            .with_for_update()
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_vault_for_update(
        self, session: AsyncSession, person_id: uuid.UUID
    ) -> PersonVault | None:
        stmt = select(PersonVault).where(PersonVault.person_id == person_id).with_for_update()
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _import_auth_fields(
        self,
        session: AsyncSession,
        person: Person,
        vault: PersonVault,
        provider_user: ProviderUser,
    ) -> None:
        await self._upsert_auth_vault_value(
            session, vault, "auth.user_id", str(provider_user.id), "auth_verified"
        )
        await self._upsert_auth_vault_value(
            session, vault, "auth.account_status", person.account_status, "auth_verified"
        )

    async def _upsert_auth_vault_value(
        self,
        session: AsyncSession,
        vault: PersonVault,
        field_key: str,
        value: Any,
        verification_level: str,
    ) -> None:
        result = await session.execute(
            select(VaultValue).where(
                VaultValue.vault_id == vault.id,
                VaultValue.field_key == field_key,
                VaultValue.status == "active",
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            if existing.value == value:
                return
            existing.status = "superseded"
            session.add(
                VaultHistory(
                    vault_id=vault.id,
                    field_key=field_key,
                    action="superseded",
                    old_value=existing.value,
                    new_value=value,
                    actor_type="system",
                    actor_id="bootstrap",
                )
            )
        row = VaultValue(
            vault_id=vault.id,
            field_key=field_key,
            value=value,
            status="active",
            verification_level=verification_level,
            confidence=1.0,
        )
        session.add(row)
        await session.flush()
        session.add(
            VaultEvidence(
                vault_value_id=row.id,
                source_type="auth",
                source_reference=AUTH_PROVIDER_NAME,
                confidence=1.0,
            )
        )
        session.add(
            VaultHistory(
                vault_id=vault.id,
                field_key=field_key,
                action="created" if existing is None else "updated",
                old_value=existing.value if existing else None,
                new_value=value,
                actor_type="system",
                actor_id="bootstrap",
            )
        )

    def _person_dict(self, person: Person) -> dict[str, Any]:
        return {
            "id": str(person.id),
            "email": person.email,
            "emailVerified": person.email_verified,
            "fullName": person.full_name,
            "preferredName": person.preferred_name,
            "phone": person.phone,
            "accountStatus": person.account_status,
            "version": person.version,
            "createdAt": person.created_at.isoformat() if person.created_at else None,
            "updatedAt": person.updated_at.isoformat() if person.updated_at else None,
            "onboardingCompleted": person.onboarding_completed_at is not None,
            "onboardingCompletedAt": (
                person.onboarding_completed_at.isoformat()
                if person.onboarding_completed_at
                else None
            ),
            "onboardingPath": person.onboarding_path,
        }

    def _vault_summary(self, vault: PersonVault, completion: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(vault.id),
            "catalogVersion": vault.catalog_version,
            "applicableScopes": vault.applicable_scopes,
            "completion": completion,
            "version": vault.version,
        }


async def get_person_by_auth(
    session: AsyncSession,
    external_auth_id: str,
    *,
    provider: str = AUTH_PROVIDER_NAME,
) -> Person:
    stmt = (
        select(Person)
        .where(
            Person.auth_provider == provider,
            Person.external_auth_id == external_auth_id,
            Person.deleted_at.is_(None),
        )
        .options(selectinload(Person.vault))
    )
    result = await session.execute(stmt)
    person = result.scalar_one_or_none()
    if person is None:
        raise PersonNotFoundError()
    return person


async def update_person_profile(
    session: AsyncSession,
    person: Person,
    *,
    expected_version: int,
    updates: dict[str, Any],
) -> Person:
    if person.version != expected_version:
        raise VersionConflictError()
    allowed = {"full_name", "preferred_name", "phone"}
    for key, val in updates.items():
        if key in allowed and val is not None:
            setattr(person, key, val)
    person.version += 1
    await session.flush()
    if person.vault:
        await apply_completion_to_vault(session, person, person.vault)
    await session.commit()
    return person


async def soft_delete_person_data(session: AsyncSession, person: Person) -> None:
    """Mark person deleted and purge vault values before auth deletion."""
    async with session.begin():
        person.account_status = "deleted"
        person.deleted_at = datetime.now(UTC)
        person.email = f"deleted-{person.id}@anonymous.local"
        person.full_name = None
        person.preferred_name = None
        person.phone = None
        person.onboarding_completed_at = None
        person.onboarding_path = None
        person.version += 1
        for model in (
            Education,
            WorkExperience,
            Project,
            Skill,
            Certification,
            Goal,
            PersonConsent,
        ):
            await session.execute(delete(model).where(model.person_id == person.id))
        if person.vault:
            await session.execute(
                delete(VaultValue).where(VaultValue.vault_id == person.vault.id)
            )
            await session.execute(
                delete(VaultHistory).where(VaultHistory.vault_id == person.vault.id)
            )
            person.vault.applicable_scopes = []
            person.vault.version += 1
