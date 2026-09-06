"""Vault Intelligence — multi-source, multi-domain Person understanding.

Architecture (AgentSpan-inspired specialists + deterministic tools):
  Source plugins (chat | document | linkedin* | social*)
       ↓
  Deterministic boosters (GPA/marks/countries/unis — high precision)
       ↓
  Omnibus multi-domain LLM specialist (education, admissions, identity, finance)
       ↓
  Normalize → merge → ExtractionBundle

* linkedin/social are registered stubs for upcoming connectors.
"""

from __future__ import annotations

from typing import Any

from pai.intelligences.vault.sources.chat import ChatSourceDomain
from pai.intelligences.vault.sources.document import DocumentSourceDomain
from pai.intelligences.vault.sources.linkedin import LinkedInSourceDomain
from pai.intelligences.vault.sources.social import SocialSourceDomain
from pai.intelligences.vault.types import (
    ExtractionBundle,
    ExtractionRequest,
    SourceKind,
)
from pai.platform.llm.gateway import LLMGateway
from pai.domains.memory.service import PersonMemoryService
from pai.kernel.contracts.schemas import VaultCandidate


class VaultIntelligenceService:
    """PAI's strong profile-learning brain. Does not write Vault itself."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        memory: PersonMemoryService | None = None,
    ) -> None:
        self._gateway = gateway
        self._memory = memory
        self._domains: dict[str, Any] = {
            SourceKind.CHAT.value: ChatSourceDomain(),
            SourceKind.DOCUMENT.value: DocumentSourceDomain(),
            SourceKind.LINKEDIN.value: LinkedInSourceDomain(),
            SourceKind.SOCIAL.value: SocialSourceDomain(),
        }

    def registered_sources(self) -> list[str]:
        return list(self._domains.keys())

    async def extract(self, request: ExtractionRequest) -> ExtractionBundle:
        domain = self._domains.get(request.source.value)
        if domain is None:
            return ExtractionBundle(
                source=request.source,
                coverage_notes=[f"unknown source {request.source}"],
            )
        bundle = await domain.extract(request, gateway=self._gateway)
        return bundle

    async def extract_from_chat(
        self,
        *,
        user_message: str,
        user_message_id: str,
        known_facts: list[str] | None = None,
        person_id: str | None = None,
    ) -> list[VaultCandidate]:
        bundle = await self.extract(
            ExtractionRequest(
                source=SourceKind.CHAT,
                text=user_message,
                source_reference=user_message_id,
                known_facts=known_facts or [],
                person_id=person_id,
            )
        )
        return bundle.candidates

    async def extract_from_document(
        self,
        *,
        document_id: str,
        document_text: str,
        document_type_hint: str = "generic",
        known_facts: list[str] | None = None,
        person_id: str | None = None,
    ) -> list[VaultCandidate]:
        bundle = await self.extract(
            ExtractionRequest(
                source=SourceKind.DOCUMENT,
                text=document_text,
                source_reference=document_id,
                document_type_hint=document_type_hint,
                known_facts=known_facts or [],
                person_id=person_id,
            )
        )
        return bundle.candidates

    async def extract_chat_bundle(
        self,
        *,
        user_message: str,
        user_message_id: str,
        known_facts: list[str] | None = None,
        person_id: str | None = None,
    ) -> ExtractionBundle:
        return await self.extract(
            ExtractionRequest(
                source=SourceKind.CHAT,
                text=user_message,
                source_reference=user_message_id,
                known_facts=known_facts or [],
                person_id=person_id,
            )
        )
