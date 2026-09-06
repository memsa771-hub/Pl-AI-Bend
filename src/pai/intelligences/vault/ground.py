"""Drop LLM facts that are not grounded in the source text."""

from __future__ import annotations

import re

from pai.kernel.contracts.schemas import VaultCandidate


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def evidence_in_source(evidence: str, source: str) -> bool:
    ev = _fold(evidence)
    src = _fold(source)
    if len(ev) < 3:
        return False
    return ev in src


def ground_candidates(candidates: list[VaultCandidate], source: str) -> list[VaultCandidate]:
    kept: list[VaultCandidate] = []
    for row in candidates:
        evidence = (row.evidence_text or "").strip()
        if not evidence:
            continue
        if evidence_in_source(evidence, source):
            kept.append(row)
    return kept
