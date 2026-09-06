from __future__ import annotations

from pai.kernel.contracts.schemas import OBSERVED_FIELD_KEY, VaultCandidate
from pai.domains.student.vault.catalog import get_catalog_field
from pai.kernel.evidence.assertion import assertion_of


def validate_candidate(candidate: VaultCandidate) -> VaultCandidate | None:
    if candidate.field_key == OBSERVED_FIELD_KEY:
        return None
    field = get_catalog_field(candidate.field_key)
    if field is None or field.derived or not field.editable:
        return None
    if not candidate.evidence_text or not candidate.source_reference:
        return None
    if candidate.confidence < 0 or candidate.confidence > 1:
        return None
    if field.storage == "vault_value":
        if not _value_matches_type(field.value_type, candidate.value):
            return None
        return candidate
    if field.storage in (
        "educations",
        "goals",
        "person",
        "work_experiences",
        "skills",
        "projects",
        "certifications",
    ):
        if field.key == "education.gpa" and isinstance(candidate.value, (int, float)):
            return candidate
        if field.value_type == "json" and isinstance(candidate.value, (dict, list, str)):
            return candidate
        if field.value_type == "string" and isinstance(candidate.value, str):
            return candidate
        if field.value_type == "number" and isinstance(candidate.value, (int, float)):
            return candidate
        return None
    return None


def _value_matches_type(value_type: str, value: object) -> bool:
    if value is None:
        return False
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type in ("json", "date"):
        return True
    return True


def policy_decision(candidate: VaultCandidate, *, from_document: bool = False) -> str:
    """Returns: accept | pending | reject"""
    field = get_catalog_field(candidate.field_key)
    if field is None:
        return "reject"
    status = assertion_of(candidate)
    if status in ("negated", "hypothetical"):
        return "reject"
    if candidate.requires_confirmation:
        return "pending"
    if field.sensitive and candidate.confidence < 0.95:
        return "pending"
    if status == "uncertain":
        return "pending"
    accept_at = 0.80 if from_document and not field.sensitive else 0.85
    if status == "inferred":
        accept_at = max(accept_at, 0.90)
    if candidate.confidence >= accept_at and not field.sensitive:
        return "accept"
    if candidate.confidence >= 0.7:
        return "pending"
    return "reject"


def verification_level_for(candidate: VaultCandidate, *, accepted: bool, from_document: bool) -> str:
    if from_document:
        return "provider_verified" if accepted else "self_reported"
    return "self_reported"
