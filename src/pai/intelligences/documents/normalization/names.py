from __future__ import annotations

from pai.intelligences.documents.identity.names import fold_name


def normalize_person_name(value: str | None) -> str | None:
    folded = fold_name(value)
    if not folded:
        return None
    return " ".join(part.capitalize() for part in folded.split())
