from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pai.intelligences.documents.config import policy
from pai.domains.documents.models import Document


def attention_state(
    doc: Document,
    *,
    open_cases: int = 0,
    expiry: datetime | None = None,
) -> str:
    if doc.deleted_at is not None or doc.lifecycle_status == "deleted":
        return "expired"
    if doc.identity_status == "mismatch" or doc.verification_status in ("conflict", "identity_mismatch"):
        return "critical_attention"
    if open_cases:
        return "critical_attention" if doc.base_criticality == "critical" else "needs_attention"
    processing = set(policy().get("processing_status_extra") or [])
    processing.update((policy().get("processing_stages") or {}).values())
    if doc.status in processing:
        return "processing"
    if doc.status == "failed":
        return "needs_attention"
    if doc.lifecycle_status == "expired":
        return "expired"
    warn_days = int(policy()["passport_expiry_warn_days"])
    if expiry is not None and expiry <= datetime.now(UTC) + timedelta(days=warn_days):
        return "critical_attention" if expiry <= datetime.now(UTC) else "needs_attention"
    return "healthy"


def journey_criticality(doc: Document, *, attention: str) -> str:
    if attention == "critical_attention":
        return "critical"
    if attention == "needs_attention" and doc.base_criticality in ("high", "critical"):
        return "critical"
    return doc.base_criticality
