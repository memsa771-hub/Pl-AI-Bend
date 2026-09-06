"""Record actual analysis inputs; never infer relevance from goal type."""
from pai.domains.student.vault.catalog import VAULT_CATALOG, get_catalog_field

# Serialization aliases, not rules about what matters to a goal.
ALIASES = {"workExperiences": "work_experiences"}

def input_snapshot(records: dict) -> dict:
    snapshot = {ALIASES.get(key, key): value for key, value in records.items()
                if key not in {"counts", "sparseFields"}}
    sparse = records.get("sparseFields") or {}
    for field in VAULT_CATALOG.values():
        if not field.sensitive and field.storage == "vault_value":
            # Missing fields are explicit inputs too: adding one must invalidate.
            snapshot[field.key] = sparse.get(field.key)
    return snapshot

def recorded_dependencies(snapshot: dict) -> list[str]:
    dependencies = set(snapshot)
    for key, value in snapshot.items():
        if isinstance(value, list):
            dependencies.update(f"{key}:{row['id']}" for row in value
                                if isinstance(row, dict) and row.get("id"))
    return sorted(dependencies)

def affects(dependencies: list[str] | None, changed: str) -> bool:
    if not dependencies:
        return True  # Legacy/failed analysis has no reliable input manifest.
    field = get_catalog_field(changed)
    key = field.storage if field and field.storage not in {"vault_value", "person"} else changed
    return key in dependencies or any(item.startswith((key + ":", key + ".")) for item in dependencies)
