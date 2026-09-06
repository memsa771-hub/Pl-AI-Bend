from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from pai.platform.database.base import Base


class Document(Base):
    """Logical Document Vault item. File bytes live on DocumentVersion."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(256))
    document_type: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="document_vault", nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), default="student", nullable=False)
    base_criticality: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    evidence_eligible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    vault_extraction_policy: Mapped[str] = mapped_column(String(16), default="extract", nullable=False)
    trust_level: Mapped[str] = mapped_column(String(32), default="student_provided", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), default="unverified", nullable=False)
    requirement_status: Mapped[str] = mapped_column(String(16), default="optional", nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    authenticity_status: Mapped[str] = mapped_column(String(32), default="unverified", nullable=False)
    identity_status: Mapped[str] = mapped_column(String(32), default="not_applicable", nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL", use_alter=True)
    )
    # Denormalized current version — list/download without a join.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    content_text: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(16), default="student", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
    )


class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(String(64), default="extract", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_document_jobs_idempotency"),
        Index("ix_document_jobs_poll", "status", "available_at"),
    )


class DocumentCandidate(Base):
    __tablename__ = "document_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    document_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_jobs.id", ondelete="SET NULL"), index=True
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(nullable=False)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MessageDocument(Base):
    """Chat references a Document Vault item. The file is not stored on the message."""

    __tablename__ = "message_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL")
    )

    __table_args__ = (UniqueConstraint("message_id", "document_id", name="uq_message_documents"),)


class DocumentRelation(Base):
    """Polymorphic links: chat, goal, application, verification, lineage."""

    __tablename__ = "document_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    related_type: Mapped[str] = mapped_column(String(32), nullable=False)
    related_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_document_relations_related", "related_type", "related_id"),
    )


class DocumentAnalysisRun(Base):
    """Immutable processing attempt. Never overwrite a completed run."""

    __tablename__ = "document_analysis_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL"), index=True
    )
    document_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_jobs.id", ondelete="SET NULL")
    )
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ocr_provider: Mapped[str | None] = mapped_column(String(64))
    ocr_model: Mapped[str | None] = mapped_column(String(128))
    classifier_version: Mapped[str | None] = mapped_column(String(32))
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    normalization_version: Mapped[str | None] = mapped_column(String(32))
    reconciliation_policy_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    current_stage: Mapped[str] = mapped_column(String(32), default="security", nullable=False)
    completed_stages: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    digitization: Mapped[dict | None] = mapped_column(JSONB)
    provider_artifact_path: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentFact(Base):
    """Normalized evidence. Not Person Vault truth."""

    __tablename__ = "document_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_analysis_runs.id", ondelete="SET NULL"), index=True
    )
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_reference: Mapped[str | None] = mapped_column(String(128))
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    normalized_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    page: Mapped[int | None] = mapped_column(Integer)
    bounding_box: Mapped[dict | None] = mapped_column(JSONB)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    normalization_confidence: Mapped[float | None] = mapped_column(Float)
    document_quality: Mapped[str | None] = mapped_column(String(32))
    source_authority: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    field_criticality: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    identity_status: Mapped[str] = mapped_column(String(32), default="not_applicable", nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), default="personal", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DocumentParty(Base):
    __tablename__ = "document_parties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="SET NULL")
    )
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_analysis_runs.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    identity_status: Mapped[str] = mapped_column(String(32), default="not_applicable", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VerificationCase(Base):
    """Persistent conflict. Counselor chat and Document Vault resolve through one service."""

    __tablename__ = "verification_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("persons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    incoming_document_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_facts.id", ondelete="SET NULL")
    )
    case_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="high", nullable=False)
    target_entity: Mapped[str | None] = mapped_column(String(128))
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)
    existing_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    incoming_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB)
    existing_evidence: Mapped[dict | None] = mapped_column(JSONB)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(48), default="open", nullable=False)
    resolution_type: Mapped[str | None] = mapped_column(String(48))
    resolution_notes: Mapped[str | None] = mapped_column(Text)
    presented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_verification_cases_open", "person_id", "status"),)
