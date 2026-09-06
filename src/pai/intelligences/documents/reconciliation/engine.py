from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from pai.intelligences.documents.config import policy
from pai.intelligences.documents.evidence.criticality import rank
from pai.intelligences.documents.reconciliation.comparators import (
    relative_delta,
    values_equivalent,
)

Decision = Literal[
    "CONFIRMS_EXISTING",
    "NEW_SAFE_FACT",
    "PROPOSE_UPDATE",
    "REQUIRES_CONFIRMATION",
    "CRITICAL_CONFLICT",
    "WRONG_SUBJECT",
    "INSUFFICIENT_EVIDENCE",
    "IGNORE_FOR_VAULT",
]


class ReconcileInput(BaseModel):
    field_key: str
    incoming_value: Any
    existing_value: Any = None
    evidence_text: str = ""
    evidence_eligible: bool = True
    identity_status: str = "not_applicable"
    source_authority: str = "none"
    field_criticality: str = "normal"
    extraction_confidence: float = 0.0
    ocr_confidence: float | None = None
    document_quality: str = "unknown"


class ReconcileResult(BaseModel):
    decision: Decision
    reason: str


def reconcile(item: ReconcileInput) -> ReconcileResult:
    rules = policy()
    if not item.evidence_eligible or item.source_authority == "none":
        return ReconcileResult(decision="IGNORE_FOR_VAULT", reason="not_authoritative")
    if item.identity_status == "mismatch":
        return ReconcileResult(decision="WRONG_SUBJECT", reason="identity_mismatch")
    if not (item.evidence_text or "").strip():
        return ReconcileResult(decision="INSUFFICIENT_EVIDENCE", reason="no_span")
    if item.document_quality in set(rules.get("unreadable_qualities") or ("unreadable",)):
        return ReconcileResult(decision="INSUFFICIENT_EVIDENCE", reason="unreadable")
    if item.identity_status in set(rules.get("unconfirmed_identity") or ()):
        return ReconcileResult(decision="REQUIRES_CONFIRMATION", reason="identity_unconfirmed")
    conf = item.extraction_confidence
    low_quality = item.document_quality in set(rules.get("low_qualities") or ("low",))
    readable = item.document_quality in set(rules.get("readable_qualities") or ("good", "unknown"))
    critical = rank(item.field_criticality) >= rank(str(rules.get("block_auto_apply_at") or "critical"))
    if item.ocr_confidence is None and critical:
        # Vision transcription has no OCR score; critical facts stay human-gated.
        if item.existing_value is None:
            return ReconcileResult(decision="PROPOSE_UPDATE", reason="critical_unscored_ocr")
        if values_equivalent(item.field_key, item.existing_value, item.incoming_value):
            return ReconcileResult(decision="CONFIRMS_EXISTING", reason="same_value")
        delta = relative_delta(item.field_key, item.existing_value, item.incoming_value)
        if delta >= float(rules["critical_relative_delta"]):
            return ReconcileResult(decision="CRITICAL_CONFLICT", reason="critical_delta")
        return ReconcileResult(decision="REQUIRES_CONFIRMATION", reason="critical_unscored_ocr")
    if item.existing_value is None:
        if (
            conf >= float(rules["new_safe_confidence"])
            and not critical
            and readable
            and not low_quality
        ):
            return ReconcileResult(decision="NEW_SAFE_FACT", reason="new_high_confidence")
        if conf >= float(rules["propose_confidence"]):
            return ReconcileResult(decision="PROPOSE_UPDATE", reason="new_needs_review")
        return ReconcileResult(decision="INSUFFICIENT_EVIDENCE", reason="low_confidence")
    if values_equivalent(item.field_key, item.existing_value, item.incoming_value):
        return ReconcileResult(decision="CONFIRMS_EXISTING", reason="same_value")
    delta = relative_delta(item.field_key, item.existing_value, item.incoming_value)
    if critical and delta >= float(rules["critical_relative_delta"]):
        return ReconcileResult(decision="CRITICAL_CONFLICT", reason="critical_delta")
    if low_quality or not readable:
        return ReconcileResult(decision="REQUIRES_CONFIRMATION", reason="low_document_quality")
    if conf >= float(rules["propose_confidence"]):
        return ReconcileResult(decision="PROPOSE_UPDATE", reason="differs")
    return ReconcileResult(decision="INSUFFICIENT_EVIDENCE", reason="weak_diff")
