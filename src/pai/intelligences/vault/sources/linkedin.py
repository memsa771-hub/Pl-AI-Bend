from __future__ import annotations

from pai.kernel.errors import AuthError
from pai.intelligences.vault.types import ExtractionBundle, ExtractionRequest, SourceKind
from pai.platform.llm.gateway import LLMGateway


class LinkedInSourceDomain:
    """Future: import public LinkedIn profile / résumé export into Vault."""

    kind = SourceKind.LINKEDIN.value

    async def extract(
        self, request: ExtractionRequest, *, gateway: LLMGateway
    ) -> ExtractionBundle:
        raise AuthError(
            code="SOURCE_NOT_ENABLED",
            message=(
                "LinkedIn Vault Intelligence is registered but not enabled yet. "
                "Chat and document sources are available now."
            ),
            status_code=501,
        )
