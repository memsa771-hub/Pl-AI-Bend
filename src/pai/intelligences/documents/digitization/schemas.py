from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DigitizationResult(BaseModel):
    text: str = ""
    method: str
    provider: str
    model: str | None = None
    language: str | None = None
    page_count: int = 1
    ocr_confidence: float | None = None
    quality: str = "unknown"
    needs_ocr: bool = False
    pages: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    raw_response: dict[str, Any] | None = None
