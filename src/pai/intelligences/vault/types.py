from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from pai.kernel.contracts.schemas import VaultCandidate


class SourceKind(StrEnum):
    CHAT = "chat"
    DOCUMENT = "document"
    LINKEDIN = "linkedin"
    SOCIAL = "social"
    THIRD_PARTY = "third_party"


class ExtractionRequest(BaseModel):
    source: SourceKind
    text: str
    source_reference: str
    known_facts: list[str] = Field(default_factory=list)
    document_type_hint: str | None = None
    person_id: str | None = None
    # Future: raw provider payload (LinkedIn JSON, etc.)
    raw_payload: dict[str, Any] | None = None


class ExtractionBundle(BaseModel):
    """Unified output of Vault Intelligence for any source."""

    candidates: list[VaultCandidate] = Field(default_factory=list)
    domains_fired: list[str] = Field(default_factory=list)
    booster_hits: list[str] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)
    provider_calls: int = 0
    source: SourceKind = SourceKind.CHAT
    meta: dict[str, Any] = Field(default_factory=dict)
    current_goal: Any | None = None
