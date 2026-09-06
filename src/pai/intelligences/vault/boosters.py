"""Compatibility shim: intelligence establishes source meaning, never keywords."""
from pai.kernel.contracts.schemas import VaultCandidate

def run_deterministic_boosters(text: str, *, source_reference: str,
                               source_type: str = "chat") -> tuple[list[VaultCandidate], list[str]]:
    return [], []
