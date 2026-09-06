"""Map aliases / messy keys onto the official Vault catalog."""

from __future__ import annotations

from typing import Any

from pai.domains.student.normalization.geo import country_codes_from_value
from pai.domains.student.normalization.vocab import BudgetBand
from pai.kernel.contracts.schemas import OBSERVED_FIELD_KEY, VaultCandidate
from pai.domains.student.vault.catalog import VAULT_CATALOG, get_catalog_field

_ALIASES: dict[str, str] = {
    "education.degree": "education.program",
    "education.qualification": "education.program",
    "education.cgpa": "education.gpa",
    "education.percentage": "education.marks",
    "education.group": "education.stream",
    "application.destination": "application.study_country",
    "application.country": "application.study_country",
    "application.target_country": "application.study_country",
    "application.goal": "application.career_interest",
    "application.program_interest": "application.career_interest",
    "career.goal": "application.career_interest",
    "location.city": "location.current_city",
    "location.country": "location.current_country",
    "identity.name": "identity.full_name",
    "identity.gender": "demographics.gender",
    "demographics.sex": "demographics.gender",
    "identity.nationality": "demographics.nationality",
    "identity.linkedin": "social.linkedin_url",
    "social.linkedin": "social.linkedin_url",
    "education.level": "education.highest_level",
    "finance.budget": "finance.funding_status",
    "mobility.countries": "mobility.preferred_regions",
    "career.job": "career.work_history",
    "career.jobs": "career.work_history",
    "career.experience": "career.work_history",
    "career.skill": "career.skills",
    "career.project": "career.projects",
    "career.certification": "career.certifications",
    "career.certs": "career.certifications",
    "OTHER_POTENTIAL_FACT": OBSERVED_FIELD_KEY,
    "other_potential_fact": OBSERVED_FIELD_KEY,
    "memory.observed": OBSERVED_FIELD_KEY,
}


def normalize_candidate(candidate: VaultCandidate) -> VaultCandidate | None:
    key = (candidate.field_key or "").strip()
    key = _ALIASES.get(key, key)
    key = _ALIASES.get(key.lower(), key) if key.lower() in _ALIASES else key
    # case-insensitive catalog match
    if key not in VAULT_CATALOG:
        lowered = {k.lower(): k for k in VAULT_CATALOG}
        key = lowered.get(key.lower(), key)
    if key == OBSERVED_FIELD_KEY:
        candidate.field_key = OBSERVED_FIELD_KEY
        if isinstance(candidate.value, str):
            candidate.value = candidate.value.strip()
        if candidate.value in (None, "", [], {}):
            return None
        if candidate.confidence > 1.0:
            candidate.confidence = 1.0
        if candidate.confidence < 0.0:
            candidate.confidence = 0.0
        if not candidate.fact_type:
            candidate.fact_type = "OTHER_POTENTIAL_FACT"
        return candidate
    field = get_catalog_field(key)
    if field is None or field.derived or not field.editable:
        return None
    candidate.field_key = key
    candidate.value = _normalize_value(key, candidate.value)
    if key == "education.gpa" and isinstance(candidate.value, dict):
        candidate.requires_confirmation |= bool(candidate.value.get("requires_confirmation"))
    if candidate.confidence > 1.0:
        candidate.confidence = 1.0
    if candidate.confidence < 0.0:
        candidate.confidence = 0.0
    return candidate


def _normalize_value(field_key: str, value: Any) -> Any:
    if field_key == "education.gpa":
        from pai.domains.student.normalization.grades import parse_grade
        return parse_grade(value) or value
    if field_key == "education.marks":
        if isinstance(value, str) and "/" in value:
            parts = value.split("/", 1)
            try:
                return {"obtained": float(parts[0].strip()), "total": float(parts[1].strip())}
            except ValueError:
                return value
        if isinstance(value, dict):
            obtained = value.get("obtained") or value.get("marks_obtained")
            total = value.get("total") or value.get("marks_total")
            if obtained is not None and total is not None:
                return {"obtained": float(obtained), "total": float(total)}
        return value
    if field_key == "application.target_universities":
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value
    if field_key in {
        "demographics.nationality",
        "location.current_country",
        "application.study_country",
    }:
        codes = country_codes_from_value(value)
        if not codes:
            return value
        return codes[0] if len(codes) == 1 else ", ".join(codes)
    if field_key == "mobility.preferred_regions":
        codes = country_codes_from_value(value)
        return codes or value
    if field_key == "finance.funding_status" and isinstance(value, str):
        token = value.strip().lower().replace(" ", "_")
        aliases = {
            "limited_budget": BudgetBand.limited.value,
            "low_budget": BudgetBand.limited.value,
        }
        token = aliases.get(token, token)
        if token in {item.value for item in BudgetBand}:
            return token
        return value
    if field_key == "education.additional_maths" and isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower(), value)
    if field_key == "finance.scholarship_interest" and isinstance(value, str):
        return {"true": True, "false": False}.get(value.strip().lower(), value)

    return value


def normalize_candidates(candidates: list[VaultCandidate]) -> list[VaultCandidate]:
    out: list[VaultCandidate] = []
    for c in candidates:
        norm = normalize_candidate(c)
        if norm is not None:
            out.append(norm)
    return out
