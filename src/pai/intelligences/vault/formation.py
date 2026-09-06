"""Separate extraction from memory selection.

Recall-first extractors may emit false positives, OTHER facts, negations,
hypotheticals, and third-party attributions. Only student-attributed,
non-hypothetical catalog facts are eligible for Vault writes. Everything
else is observed memory — additive evidence, not a Vault mutation.
"""

from __future__ import annotations

from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.evidence.assertion import assertion_of, format_observed, is_vault_eligible

__all__ = ["assertion_of", "format_observed", "is_vault_eligible", "partition_candidates"]


def partition_candidates(
    candidates: list[VaultCandidate],
) -> tuple[list[VaultCandidate], list[VaultCandidate]]:
    vault: list[VaultCandidate] = []
    observed: list[VaultCandidate] = []
    for row in candidates:
        (vault if is_vault_eligible(row) else observed).append(row)
    return vault, observed
