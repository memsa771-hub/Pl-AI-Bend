from __future__ import annotations

from pai.intelligences.documents.config import policy, taxonomy


def known_types() -> set[str]:
    return set(taxonomy()["types"])


def default_type() -> str:
    return str(taxonomy().get("default_type") or "other")


def type_meta(document_type: str) -> dict:
    types = taxonomy()["types"]
    return dict(types.get(document_type) or types[default_type()])


def _usable_hint(hint: str | None) -> str | None:
    token = (hint or "").strip().lower()
    if not token or token in {"string", "other", default_type()}:
        return None
    return token if token in known_types() else None


def classify_from_name(filename: str, hint: str | None = None, text: str = "") -> str:
    """Compatibility API: filenames cannot establish document type or authority."""
    return _usable_hint(hint) or default_type()


def evidence_eligible(*, source_type: str, document_type: str) -> bool:
    rules = policy()
    if source_type in set(rules.get("generated_sources") or []):
        return False
    blocked = set(rules.get("evidence_ineligible_types") or [])
    return document_type not in blocked


def normalize_source_type(value: str | None, *, default: str = "document_vault") -> str:
    allowed = set(taxonomy()["source_types"])
    return value if value in allowed else default


def normalize_created_by(value: str | None, *, default: str = "student") -> str:
    allowed = set(taxonomy()["created_by"])
    return value if value in allowed else default
