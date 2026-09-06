from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.documents.models import (
    Document,
    DocumentCandidate,
    DocumentJob,
    DocumentVersion,
    MessageDocument,
)
from pai.domains.documents.relations import add_relation
from pai.domains.student.person.models import Person, PersonVault, VaultValue
from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.errors import AuthError


class DocumentNotFoundError(AuthError):
    def __init__(self) -> None:
        super().__init__(code="DOCUMENT_NOT_FOUND", message="Document not found.", status_code=404)


class DocumentIdentityUnresolvedError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            code="DOCUMENT_IDENTITY_UNRESOLVED",
            message="Resolve whether this document belongs to you before accepting extracted facts.",
            status_code=409,
        )


async def enqueue_reprocess(session: AsyncSession, person_id: uuid.UUID, document_id: uuid.UUID) -> None:
    doc = await get_document_owned(session, person_id, document_id)
    job = DocumentJob(
        document_id=doc.id,
        document_version_id=doc.current_version_id,
        person_id=person_id,
        idempotency_key=f"reprocess-{doc.id}-{uuid.uuid4()}",
        status="pending",
    )
    session.add(job)
    doc.status = "queued"
    await session.commit()


async def get_document_owned(
    session: AsyncSession, person_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    result = await session.execute(
        select(Document).where(
            Document.id == document_id,
            Document.person_id == person_id,
            Document.deleted_at.is_(None),
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise DocumentNotFoundError()
    return doc


async def list_documents(session: AsyncSession, person_id: uuid.UUID) -> list[Document]:
    result = await session.execute(
        select(Document)
        .where(Document.person_id == person_id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
    )
    return list(result.scalars().all())


async def attach_documents_to_message(
    session: AsyncSession,
    person_id: uuid.UUID,
    message_id: uuid.UUID,
    document_ids: list[uuid.UUID],
) -> list[Document]:
    if not document_ids:
        return []
    unique = list(dict.fromkeys(document_ids))
    result = await session.execute(
        select(Document).where(
            Document.person_id == person_id,
            Document.deleted_at.is_(None),
            Document.id.in_(unique),
        )
    )
    docs = list(result.scalars().all())
    if len(docs) != len(unique):
        raise DocumentNotFoundError()
    by_id = {row.id: row for row in docs}
    for doc_id in unique:
        doc = by_id[doc_id]
        session.add(
            MessageDocument(
                message_id=message_id,
                document_id=doc.id,
                document_version_id=doc.current_version_id,
            )
        )
        add_relation(
            session,
            document_id=doc.id,
            version_id=doc.current_version_id,
            relation_type="attached_to",
            related_type="message",
            related_id=str(message_id),
        )
    await session.commit()
    return [by_id[doc_id] for doc_id in unique]


async def attachment_note_for_message(session: AsyncSession, message_id: uuid.UUID) -> str:
    result = await session.execute(
        select(Document)
        .join(MessageDocument, MessageDocument.document_id == Document.id)
        .where(MessageDocument.message_id == message_id, Document.deleted_at.is_(None))
    )
    rows = list(result.scalars().all())
    if not rows:
        return ""
    names = [f"{d.original_filename} ({d.document_type or 'other'})" for d in rows]
    return "Attached documents: " + ", ".join(names)


async def _current_vault_values(
    session: AsyncSession, person: Person, field_keys: list[str]
) -> dict[str, object]:
    if not field_keys:
        return {}
    vault_id = await session.scalar(
        select(PersonVault.id).where(PersonVault.person_id == person.id)
    )
    if vault_id is None:
        return {}
    result = await session.execute(
        select(VaultValue.field_key, VaultValue.value).where(
            VaultValue.vault_id == vault_id,
            VaultValue.field_key.in_(field_keys),
            VaultValue.status.in_(("active", "pending_confirmation")),
        )
    )
    return {key: value for key, value in result.all()}


async def list_document_candidates(
    session: AsyncSession, person: Person, document_id: uuid.UUID
) -> list[dict]:
    doc = await get_document_owned(session, person.id, document_id)
    result = await session.execute(
        select(DocumentCandidate)
        .where(
            DocumentCandidate.document_id == doc.id,
            DocumentCandidate.person_id == person.id,
        )
        .order_by(DocumentCandidate.created_at.desc())
    )
    rows = list(result.scalars().all())
    current = await _current_vault_values(session, person, [row.field_key for row in rows])
    return [
        {
            "id": str(row.id),
            "fieldKey": row.field_key,
            "value": row.value,
            "evidenceText": row.evidence_text,
            "confidence": row.confidence,
            "reviewStatus": row.review_status,
            "reason": row.reasoning_summary,
            "currentVaultValue": current.get(row.field_key),
            "documentVersionId": str(row.document_version_id) if row.document_version_id else None,
            "documentJobId": str(row.document_job_id) if row.document_job_id else None,
        }
        for row in rows
    ]


async def review_document_candidates(
    session: AsyncSession,
    person: Person,
    document_id: uuid.UUID,
    *,
    accept_ids: list[uuid.UUID],
    reject_ids: list[uuid.UUID] | None = None,
) -> tuple[set[str], list[VaultCandidate]]:
    doc = await get_document_owned(session, person.id, document_id)
    reject_ids = reject_ids or []
    requested = list(dict.fromkeys([*accept_ids, *reject_ids]))
    if requested:
        found = await session.scalars(
            select(DocumentCandidate.id).where(
                DocumentCandidate.document_id == doc.id,
                DocumentCandidate.person_id == person.id,
                DocumentCandidate.id.in_(requested),
            )
        )
        if not list(found.all()):
            raise AuthError(
                code="CANDIDATE_NOT_FOUND",
                message="Those ids are not candidates for this document. Copy ids from GET /documents/{id}/candidates.",
                status_code=400,
            )
    if accept_ids and doc.identity_status == "mismatch":
        raise DocumentIdentityUnresolvedError()
    if reject_ids:
        result = await session.execute(
            select(DocumentCandidate).where(
                DocumentCandidate.document_id == doc.id,
                DocumentCandidate.person_id == person.id,
                DocumentCandidate.id.in_(reject_ids),
            )
        )
        for row in result.scalars():
            row.review_status = "rejected"
    to_apply: list[VaultCandidate] = []
    if accept_ids:
        result = await session.execute(
            select(DocumentCandidate).where(
                DocumentCandidate.document_id == doc.id,
                DocumentCandidate.person_id == person.id,
                DocumentCandidate.id.in_(accept_ids),
            )
        )
        for row in result.scalars():
            row.review_status = "accepted"
            to_apply.append(
                VaultCandidate(
                    field_key=row.field_key,
                    value=row.value,
                    confidence=row.confidence,
                    evidence_text=row.evidence_text or "",
                    source_type="document",
                    source_reference=str(doc.id),
                    rationale_summary=row.reasoning_summary or "",
                )
            )
    leftover = await session.execute(
        select(DocumentCandidate.id).where(
            DocumentCandidate.document_id == doc.id,
            DocumentCandidate.review_status == "pending",
        )
    )
    doc.status = "awaiting_review" if leftover.first() is not None else "processed"
    return {row.field_key for row in to_apply}, to_apply


async def list_document_storage_paths(
    session: AsyncSession,
    person: Person,
    document_id: uuid.UUID,
) -> list[str]:
    doc = await get_document_owned(session, person.id, document_id)
    versions = await session.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
    )
    paths = {doc.storage_path}
    paths.update(row.storage_path for row in versions.scalars())
    return [path for path in paths if path]


async def mark_document_deleted(
    session: AsyncSession,
    person: Person,
    document_id: uuid.UUID,
) -> None:
    doc = await get_document_owned(session, person.id, document_id)
    doc.deleted_at = datetime.now(UTC)
    doc.status = "deleted"
