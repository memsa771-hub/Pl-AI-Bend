from __future__ import annotations

from pai.intelligences.documents.classification.taxonomy import default_type
from pai.intelligences.documents.config import policy


def source_authority(document_type: str, field_key: str) -> str:
    matrix = policy()["authority"]
    row = matrix.get(document_type) or matrix.get(default_type()) or {}
    return str(row.get(field_key) or row.get("*") or "none")
