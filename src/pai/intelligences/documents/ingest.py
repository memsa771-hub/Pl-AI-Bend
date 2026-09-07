"""Upload-time document ingest. Classification/scan/storage happen here; domain persists."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings
from pai.domains.documents.models import Document, DocumentJob, DocumentVersion
from pai.domains.student.person.models import Person
from pai.intelligences.documents.classification.classifier import classify_document
from pai.intelligences.documents.classification.taxonomy import (
    evidence_eligible,
    normalize_created_by,
    normalize_source_type,
)
from pai.intelligences.documents.security.scanner import scan_bytes
from pai.intelligences.documents.security.validation import validate_upload_bytes
from pai.platform.storage.supabase import SupabaseStorageProvider


async def create_document_upload(
    session: AsyncSession,
    settings: Settings,
    person: Person,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    storage: SupabaseStorageProvider,
    source_type: str = "document_vault",
    document_type: str | None = None,
    created_by: str = "student",
    title: str | None = None,
    inline_processing: bool = False,
) -> Document:
    mime = validate_upload_bytes(filename, content_type, data, settings)
    await scan_bytes(data, filename=filename, settings=settings)
    source = normalize_source_type(source_type)
    origin = "pai_generated" if source == "ai_generated" else "user_uploaded"
    actor = normalize_created_by(created_by)
    classified = classify_document(filename=filename, hint=document_type, source_type=source)
    eligible = evidence_eligible(source_type=source, document_type=classified["document_type"])
    policy = "extract" if eligible else "disabled"
    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()
    path = f"{person.id}/{doc_id}/{version_id}/original"
    from pai.domains.student.person.write_lock import lock_person
    await lock_person(session, person.id)
    await storage.upload_private(path, data, mime)
    try:
        digest = hashlib.sha256(data).hexdigest()
        doc = Document(
            id=doc_id,
            person_id=person.id,
            title=(title or filename)[:256],
            document_type=classified["document_type"],
            category=classified["category"],
            source_type=source,
            origin=origin,
            created_by=actor,
            base_criticality=classified["base_criticality"],
            evidence_eligible=eligible,
            vault_extraction_policy=policy,
            trust_level=classified["trust_level"],
            storage_path=path,
            original_filename=filename,
            mime_type=mime,
            size_bytes=len(data),
            status="ready" if source == "ai_generated" else "uploaded",
            lifecycle_status="draft" if source == "ai_generated" else "active",
        )
        session.add(doc)
        await session.flush()
        version = DocumentVersion(
            id=version_id,
            document_id=doc.id,
            version_number=1,
            storage_path=path,
            original_filename=filename,
            mime_type=mime,
            size_bytes=len(data),
            sha256=digest,
            created_by=actor,
        )
        session.add(version)
        await session.flush()
        doc.current_version_id = version.id
        if source != "ai_generated":
            session.add(
                DocumentJob(
                    document_id=doc.id,
                    document_version_id=version.id,
                    person_id=person.id,
                    idempotency_key=f"extract-{version.id}",
                    status="processing" if inline_processing else "pending",
                    attempts=1 if inline_processing else 0,
                    locked_at=datetime.now(UTC) if inline_processing else None,
                )
            )
        await session.commit()
    except Exception:
        await session.rollback()
        await storage.delete_object(path)
        raise
    await session.refresh(doc)
    return doc
