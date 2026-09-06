from __future__ import annotations

from pai.kernel.errors import AuthError
from pai.intelligences.vault.types import ExtractionBundle, ExtractionRequest, SourceKind
from pai.platform.llm.gateway import LLMGateway


class SocialSourceDomain:
    """Future: social / third-party profile signals into Vault."""

    kind = SourceKind.SOCIAL.value

    async def extract(
        self, request: ExtractionRequest, *, gateway: LLMGateway
    ) -> ExtractionBundle:
        raise AuthError(
            code="SOURCE_NOT_ENABLED",
            message=(
                "Social Vault Intelligence is registered but not enabled yet. "
                "Chat and document sources are available now."
            ),
            status_code=501,
        )
