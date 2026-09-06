from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from pai.kernel.errors import AuthError
from pai.platform.llm.schemas import LLMRequest, LLMResponse


class LLMProviderError(AuthError):
    def __init__(self, message: str = "LLM request failed.") -> None:
        super().__init__(code="LLM_ERROR", message=message, status_code=502)


class LLMProvider(Protocol):
    name: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    async def generate_structured(
        self,
        request: LLMRequest,
        output_schema: type[BaseModel],
    ) -> BaseModel: ...
