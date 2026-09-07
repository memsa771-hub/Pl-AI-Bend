from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pai.platform.database.base import Base


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_auth_id: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(256))
    preferred_name: Mapped[str | None] = mapped_column(String(256))
    phone: Mapped[str | None] = mapped_column(String(64))
    account_status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Null for every new user. Set after a successful form submit or CV extract.
    # Login/signup never set this.
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarding_path: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    vault: Mapped[PersonVault | None] = relationship(back_populates="person", uselist=False)

    __table_args__ = (
        UniqueConstraint("auth_provider", "external_auth_id", name="uq_persons_auth_identity"),
        Index("ix_persons_email_lower", func.lower(email)),
    )


class PersonVault(Base):
    __tablename__ = "person_vaults"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    catalog_version: Mapped[str] = mapped_column(String(32), nullable=False)
    applicable_scopes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    critical_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    important_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enrichment_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overall_completion: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    person: Mapped[Person] = relationship(back_populates="vault")
    values: Mapped[list[VaultValue]] = relationship(back_populates="vault")


class Education(Base):
    __tablename__ = "educations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    institution: Mapped[str | None] = mapped_column(String(256), nullable=True)
    qualification_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    degree: Mapped[str | None] = mapped_column(String(128))
    major: Mapped[str | None] = mapped_column(String(128))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    graduation_year: Mapped[int | None] = mapped_column(Integer)
    gpa: Mapped[float | None] = mapped_column()
    gpa_scale: Mapped[float | None] = mapped_column()
    percentage: Mapped[float | None] = mapped_column()
    status: Mapped[str | None] = mapped_column(String(64))
    # Qualification identity: the wording the student used is preserved in
    # `original_name`; `canonical_level` is the cross-country mapping used for
    # timeline ordering and gap detection (see education/levels.py).
    original_name: Mapped[str | None] = mapped_column(String(256))
    canonical_level: Mapped[str | None] = mapped_column(String(32), index=True)
    framework: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkExperience(Base):
    __tablename__ = "work_experiences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    employment_type: Mapped[str | None] = mapped_column(String(64))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(String(128))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    proficiency: Mapped[str | None] = mapped_column(String(64))
    years_experience: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(256))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    credential_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class VaultValue(Base):
    __tablename__ = "vault_values"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person_vaults.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    value_encrypted: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    verification_level: Mapped[str] = mapped_column(String(32), default="self_reported", nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("vault_values.id"))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    vault: Mapped[PersonVault] = relationship(back_populates="values")

    __table_args__ = (Index("ix_vault_values_vault_field", "vault_id", "field_key"),)


class VaultEvidence(Base):
    """Provenance for a single known fact.

    Originally scoped to `VaultValue` rows. Typed entities (education, work,
    tests, …) need the same provenance, so evidence now targets *either* a
    vault value *or* an (entity_type, entity_id, attribute) triple — one
    relation instead of duplicating evidence columns per table (doc §23).
    """

    __tablename__ = "vault_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vault_value_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vault_values.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Which attribute of the entity this evidence supports (e.g. "percentage").
    attribute: Mapped[str | None] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(512))
    evidence_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    verification_level: Mapped[str] = mapped_column(
        String(32), default="self_reported", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_vault_evidence_entity", "entity_type", "entity_id", "attribute"),
        CheckConstraint(
            "(vault_value_id IS NOT NULL) OR (entity_type IS NOT NULL AND entity_id IS NOT NULL)",
            name="ck_vault_evidence_target",
        ),
    )


class VaultHistory(Base):
    __tablename__ = "vault_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vault_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("person_vaults.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    new_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256))
    # Set when the change was to a typed entity attribute rather than a vault
    # field, so entity history is queryable without a parallel table (doc §24).
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_vault_history_entity", "entity_type", "entity_id"),)


class TestAttempt(Base):
    """One sitting of a standardized test.

    Attempts are additive: a later IELTS never overwrites an earlier one, so
    score history and expiry stay inspectable (doc §20).
    """

    __tablename__ = "test_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_type: Mapped[str] = mapped_column(String(32), nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(128))
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    test_date: Mapped[date | None] = mapped_column(Date)
    overall_score: Mapped[str | None] = mapped_column(String(32))
    overall_numeric: Mapped[float | None] = mapped_column()
    sections: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    validity_until: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="valid", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "person_id", "test_type", "attempt_number", name="uq_test_attempts_attempt"
        ),
    )


class ProfileIssue(Base):
    """Something PAI noticed about the student model that it did not silently fix.

    Covers timeline gaps, contradictions, ambiguity and duplicates across any
    domain (doc §14). Detecting an issue is not an error and does not block a
    write; it makes the uncertainty explicit so discovery can decide whether it
    is worth one natural question (doc §15).
    """

    __tablename__ = "profile_issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    # Stable identity for a re-detected issue, so validation is idempotent.
    fingerprint: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    related_entity_type: Mapped[str | None] = mapped_column(String(32))
    related_entity_ids: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    detected_from: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    clarification_needed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    clarification_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    resolution: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("person_id", "fingerprint", name="uq_profile_issues_fingerprint"),
        Index("ix_profile_issues_person_status", "person_id", "status"),
    )


class PersonConsent(Base):
    __tablename__ = "person_consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("person_id", "category", name="uq_person_consents_category"),)
