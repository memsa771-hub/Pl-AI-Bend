from __future__ import annotations

from pai.intelligences.documents.classification.taxonomy import type_meta


def expected_roles(document_type: str) -> tuple[str, ...]:
    roles = type_meta(document_type).get("party_roles") or ["subject"]
    return tuple(str(role) for role in roles)
