from __future__ import annotations

from typing import Any

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.goals.models import Goal
from pai.domains.student.person.models import (
    Certification,
    Education,
    Person,
    PersonVault,
    Project,
    Skill,
    VaultValue,
    WorkExperience,
)
from pai.domains.student.person.profile_snapshot import load_typed_profile_records
from pai.domains.student.vault.catalog import VAULT_CATALOG, CatalogField, Priority

# (storage_key used by catalog, ORM model, API camelCase for typedResources)
_TYPED_MODELS: list[tuple[str, type, str]] = [
    ("educations", Education, "educations"),
    ("work_experiences", WorkExperience, "workExperiences"),
    ("projects", Project, "projects"),
    ("skills", Skill, "skills"),
    ("certifications", Certification, "certifications"),
    ("goals", Goal, "goals"),
]


def _scope_fields(scopes: list[str]) -> list[CatalogField]:
    applicable = set(scopes)
    return [f for f in VAULT_CATALOG.values() if f.applicable_scope in applicable]


def priority_name(p: Priority) -> str:
    return {"C": "critical", "I": "important", "E": "enrichment"}[p]


async def load_presence_snapshot(
    session: AsyncSession,
    person: Person,
    vault: PersonVault | None,
) -> dict[str, Any]:
    """One-shot DB snapshot for completion / status (no per-field N+1)."""
    active_keys: set[str] = set()
    if vault is not None:
        result = await session.execute(
            select(VaultValue.field_key).where(
                VaultValue.vault_id == vault.id,
                VaultValue.status == "active",
            )
        )
        active_keys = set(result.scalars().all())

    typed_counts: dict[str, int] = {}
    typed_present: dict[str, bool] = {}
    typed_resources: dict[str, str] = {}
    if _TYPED_MODELS:
        count_parts = [
            select(literal(storage_key).label("k"), func.count().label("n"))
            .select_from(model)
            .where(model.person_id == person.id)
            for storage_key, model, _api_name in _TYPED_MODELS
        ]
        rows = (await session.execute(union_all(*count_parts))).all()
        by_key = {str(key): int(n or 0) for key, n in rows}
        for storage_key, _model, api_name in _TYPED_MODELS:
            n = by_key.get(storage_key, 0)
            typed_counts[storage_key] = n
            typed_present[storage_key] = n > 0
            typed_resources[api_name] = str(n)

    return {
        "active_keys": active_keys,
        "typed_present": typed_present,
        "typed_counts": typed_counts,
        "typed_resources": typed_resources,
    }


def field_is_present_in_snapshot(
    person: Person,
    field: CatalogField,
    snapshot: dict[str, Any],
) -> bool:
    if field.storage == "person":
        col = field.person_column
        if not col:
            return False
        return getattr(person, col) not in (None, "")
    if field.storage == "vault_value":
        return field.key in snapshot["active_keys"]
    return bool(snapshot["typed_present"].get(field.storage, False))


def compute_completion_from_snapshot(
    person: Person,
    vault: PersonVault,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    scopes: list[str] = list(vault.applicable_scopes or ["universal"])
    fields = _scope_fields(scopes)
    by_priority: dict[Priority, list[CatalogField]] = {"C": [], "I": [], "E": []}
    for field in fields:
        by_priority[field.priority].append(field)

    missing_critical: list[str] = []
    missing_important: list[str] = []
    missing_enrichment: list[str] = []
    scores: dict[str, int] = {}
    present_map: dict[str, bool] = {}

    for field in fields:
        present_map[field.key] = field_is_present_in_snapshot(person, field, snapshot)

    for priority in ("C", "I", "E"):
        group = by_priority[priority]
        if not group:
            scores[priority_name(priority)] = 0
            continue
        filled = sum(1 for f in group if present_map[f.key])
        scores[priority_name(priority)] = round(100 * filled / len(group))
        missing_keys = [f.key for f in group if not present_map[f.key]]
        if priority == "C":
            missing_critical = missing_keys
        elif priority == "I":
            missing_important = missing_keys
        else:
            missing_enrichment = missing_keys

    overall = (
        round(100 * sum(1 for f in fields if present_map[f.key]) / len(fields)) if fields else 0
    )

    next_field: dict[str, Any] = {}
    for field in sorted(fields, key=lambda f: (f.priority, f.key)):
        if not present_map[field.key]:
            next_field = {"key": field.key, "priority": field.priority, "section": field.section}
            break

    return {
        "critical": scores.get("critical", 0),
        "important": scores.get("important", 0),
        "enrichment": scores.get("enrichment", 0),
        "overall": overall,
        "applicableScopes": scopes,
        "missingCriticalFields": missing_critical,
        "missingImportantFields": missing_important,
        "missingEnrichmentFields": missing_enrichment,
        "nextRecommendedField": next_field,
        "_present_map": present_map,
    }


async def compute_completion(
    session: AsyncSession,
    person: Person,
    vault: PersonVault,
    *,
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snap = snapshot or await load_presence_snapshot(session, person, vault)
    result = compute_completion_from_snapshot(person, vault, snap)
    result.pop("_present_map", None)
    return result


def _field_label(field: CatalogField) -> str:
    return field.key.replace(".", " · ").replace("_", " ")


def _field_value(
    person: Person,
    field: CatalogField,
    sparse: dict[str, Any],
    typed: dict[str, Any],
    *,
    include_sensitive: bool,
) -> Any:
    if field.storage == "vault_value" and field.key in sparse:
        entry = sparse[field.key]
        if isinstance(entry, dict) and "value" in entry:
            return entry["value"]
        return entry
    if field.storage == "person" and field.person_column:
        raw = getattr(person, field.person_column, None)
        if field.sensitive and not include_sensitive and raw not in (None, ""):
            return "[sensitive]"
        return raw
    storage_to_typed = {
        "educations": "educations",
        "work_experiences": "workExperiences",
        "projects": "projects",
        "skills": "skills",
        "certifications": "certifications",
        "goals": "goals",
    }
    api_name = storage_to_typed.get(field.storage)
    if api_name:
        return typed.get(api_name) or []
    return None


async def build_vault_status(
    session: AsyncSession,
    person: Person,
    *,
    include_sensitive: bool = False,
) -> dict[str, Any]:
    """Whole-student picture: filled, empty, and still required."""
    vault = person.vault
    if vault is None:
        return {
            "person": {
                "id": str(person.id),
                "fullName": person.full_name,
                "email": person.email,
                "onboardingCompleted": False,
            },
            "completion": {"critical": 0, "important": 0, "enrichment": 0, "overall": 0},
            "filled": [],
            "empty": [],
            "required": [],
            "filledCount": 0,
            "emptyCount": 0,
            "requiredCount": 0,
            "sections": {},
            "typed": {},
            "nextRecommendedField": {},
            "memory": {
                "engine": "agentspan",
                "role": "Unstructured counseling insights. Structured facts live in this Vault.",
            },
        }

    from pai.domains.student.vault.service import VaultService, grow_vault_schema

    if grow_vault_schema(vault):
        # Persist catalog/scopes only — a GET must not bump optimistic-lock version.
        await session.commit()

    typed = await load_typed_profile_records(session, person.id)
    unified = await VaultService().get_unified_vault(
        session,
        person,
        include_sensitive=include_sensitive,
        typed_records=typed,
    )
    sparse = unified.get("sparseFields") or {}
    completion = unified.get("completion") or {}
    typed_present = {
        "educations": bool(typed.get("educations")),
        "work_experiences": bool(typed.get("workExperiences")),
        "projects": bool(typed.get("projects")),
        "skills": bool(typed.get("skills")),
        "certifications": bool(typed.get("certifications")),
        "goals": bool(typed.get("goals")),
    }
    snapshot = {"active_keys": set(sparse.keys()), "typed_present": typed_present}
    scopes: list[str] = list(vault.applicable_scopes or ["universal"])
    fields = [f for f in _scope_fields(scopes) if not f.derived]

    filled: list[dict[str, Any]] = []
    empty: list[dict[str, Any]] = []
    required: list[dict[str, Any]] = []
    sections: dict[str, dict[str, int]] = {}

    for field in sorted(fields, key=lambda f: (f.priority, f.section, f.key)):
        present = field_is_present_in_snapshot(person, field, snapshot)
        item = {
            "key": field.key,
            "label": _field_label(field),
            "section": field.section,
            "priority": field.priority,
            "priorityLabel": priority_name(field.priority),
            "sensitive": field.sensitive,
        }
        bucket = sections.setdefault(
            field.section, {"filled": 0, "empty": 0, "required": 0}
        )
        if present:
            item["value"] = _field_value(
                person, field, sparse, typed, include_sensitive=include_sensitive
            )
            filled.append(item)
            bucket["filled"] += 1
        elif field.priority == "C":
            required.append(item)
            bucket["required"] += 1
        else:
            empty.append(item)
            bucket["empty"] += 1

    next_field = required[0] if required else (empty[0] if empty else {})

    return {
        "vaultId": unified.get("vaultId"),
        "catalogVersion": unified.get("catalogVersion"),
        "applicableScopes": scopes,
        "version": unified.get("version"),
        "person": {
            "id": str(person.id),
            "fullName": person.full_name,
            "email": person.email,
            "phone": person.phone if include_sensitive else ("***" if person.phone else None),
            "onboardingCompleted": person.onboarding_completed_at is not None,
            "onboardingPath": person.onboarding_path,
        },
        "completion": {
            "critical": completion.get("critical", 0),
            "important": completion.get("important", 0),
            "enrichment": completion.get("enrichment", 0),
            "overall": completion.get("overall", 0),
        },
        "filled": filled,
        "required": required,
        "empty": empty,
        "missing": required + empty,
        "filledCount": len(filled),
        "emptyCount": len(empty),
        "requiredCount": len(required),
        "missingCount": len(required) + len(empty),
        "sections": sections,
        "typed": typed,
        "sparseFields": sparse,
        "typedResources": unified.get("typedResources") or typed.get("counts") or {},
        "nextRecommendedField": next_field,
        "memory": {
            "engine": "agentspan",
            "role": "Unstructured counseling insights. Structured facts live in this Vault.",
        },
    }


async def apply_completion_to_vault(
    session: AsyncSession,
    person: Person,
    vault: PersonVault,
) -> dict[str, Any]:
    result = await compute_completion(session, person, vault)
    vault.critical_completion = result["critical"]
    vault.important_completion = result["important"]
    vault.enrichment_completion = result["enrichment"]
    vault.overall_completion = result["overall"]
    vault.version += 1
    return result
