from __future__ import annotations

from typing import Protocol

from pai.intelligences.documents.digitization.schemas import DigitizationResult


class DocumentOCRProvider(Protocol):
    name: str

    def configured(self) -> bool: ...

    async def digitize(self, data: bytes, *, mime_type: str, filename: str) -> DigitizationResult: ...
