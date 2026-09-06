from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.documents.models import DocumentRelation


def add_relation(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    relation_type: str,
    related_type: str,
    related_id: str,
    version_id: uuid.UUID | None = None,
) -> DocumentRelation:
    row = DocumentRelation(
        document_id=document_id,
        document_version_id=version_id,
        relation_type=relation_type,
        related_type=related_type,
        related_id=related_id,
    )
    session.add(row)
    return row
