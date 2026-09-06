from __future__ import annotations
from pai.intelligences.vault.llm_extractor import OmnibusLLMExtractor
from pai.intelligences.vault.normalize import normalize_candidates
from pai.intelligences.vault.types import ExtractionBundle, ExtractionRequest, SourceKind
from pai.platform.llm.gateway import LLMGateway

class ChatSourceDomain:
    kind = SourceKind.CHAT.value
    async def extract(self, request: ExtractionRequest, *, gateway: LLMGateway) -> ExtractionBundle:
        extractor = OmnibusLLMExtractor(gateway)
        candidates = await extractor.extract(request)
        return ExtractionBundle(
            candidates=normalize_candidates(candidates), domains_fired=["omnibus_llm"],
            booster_hits=[], provider_calls=1, source=SourceKind.CHAT,
            current_goal=extractor.last_goal,
        )
