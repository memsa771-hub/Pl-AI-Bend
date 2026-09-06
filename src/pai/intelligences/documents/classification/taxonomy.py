from __future__ import annotations

import re

from pai.intelligences.documents.config import policy, taxonomy


def known_types() -> set[str]:
    return set(taxonomy()["types"])


def default_type() -> str:
    return str(taxonomy().get("default_type") or "other")


def type_meta(document_type: str) -> dict:
    types = taxonomy()["types"]
    return dict(types.get(document_type) or types[default_type()])


def _generated_types() -> set[str]:
    return set(taxonomy().get("generated_types") or ())


def _usable_hint(hint: str | None) -> str | None:
    token = (hint or "").strip().lower()
    if not token or token in {"string", "other", default_type()}:
        return None
    return token if token in known_types() else None


def _filename_tokens(filename: str) -> set[str]:
    skip = {"pdf", "doc", "docx", "png", "jpg", "jpeg", "txt", "bin"}
    return {
        token
        for token in re.split(r"[^a-z0-9]+", (filename or "").lower())
        if token and token not in skip
    }


def classify_from_name(filename: str, hint: str | None = None, text: str = "") -> str:
    generated = _generated_types()
    from_file = _best_type_on_filename(filename)
    if from_file:
        return from_file
    cleaned = _usable_hint(hint)
    if cleaned and cleaned not in generated:
        return cleaned
    from_text = _best_type(text, skip_types=generated)
    if from_text:
        return from_text
    if cleaned:
        return cleaned
    return default_type()


def _needle_hit(text: str, needle: str) -> bool:
    token = (needle or "").strip().lower()
    if not token:
        return False
    if " " in token:
        return token in text
    return re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text) is not None


def _best_type_on_filename(filename: str) -> str | None:
    """Whole filename tokens only. 'recommendation' in a CV body must not win."""
    tokens = _filename_tokens(filename)
    lowered = (filename or "").lower()
    best_type = None
    best_len = 0
    for rule in taxonomy()["filename_hints"]:
        for needle in rule["needles"]:
            token = (needle or "").strip().lower()
            if not token:
                continue
            hit = False
            if " " in token:
                hit = token in lowered
            else:
                hit = token in tokens
            if hit and len(token) > best_len:
                best_len = len(token)
                best_type = rule["type"]
    return best_type


def _best_type(blob: str, *, skip_types: set[str] | None = None) -> str | None:
    text = (blob or "").lower()
    if not text:
        return None
    skip = skip_types or set()
    best_type = None
    best_len = 0
    for rule in taxonomy()["filename_hints"]:
        if rule["type"] in skip:
            continue
        hit = max((len(token) for token in rule["needles"] if _needle_hit(text, token)), default=0)
        if hit > best_len:
            best_len = hit
            best_type = rule["type"]
    return best_type


def evidence_eligible(*, source_type: str, document_type: str) -> bool:
    rules = policy()
    if source_type in set(rules.get("generated_sources") or []):
        return False
    blocked = set(taxonomy().get("generated_types") or []) | set(rules.get("evidence_ineligible_types") or [])
    return document_type not in blocked


def normalize_source_type(value: str | None, *, default: str = "document_vault") -> str:
    allowed = set(taxonomy()["source_types"])
    return value if value in allowed else default


def normalize_created_by(value: str | None, *, default: str = "student") -> str:
    allowed = set(taxonomy()["created_by"])
    return value if value in allowed else default
