"""Evidence must appear in digitized text. Hallucinated spans are not document evidence."""

from __future__ import annotations

import re

_SPACE = re.compile(r"\s+")
_ALNUM = re.compile(r"[^a-z0-9]+")


def fold_span(value: str | None) -> str:
    return _SPACE.sub(" ", (value or "").casefold()).strip()


def compact_span(value: str | None) -> str:
    return _ALNUM.sub("", (value or "").casefold())


def evidence_grounded(span: str | None, document_text: str | None) -> bool:
    needle, hay = fold_span(span), fold_span(document_text)
    if len(needle) < 4 or not hay:
        return False
    if needle in hay:
        return True
    compact_needle, compact_hay = compact_span(span), compact_span(document_text)
    return len(compact_needle) >= 8 and compact_needle in compact_hay


def page_for_span(span: str | None, pages: list[dict] | None) -> int | None:
    for row in pages or []:
        if evidence_grounded(span, str(row.get("text") or "")):
            page = row.get("page")
            return int(page) if page is not None else None
    return None


def extraction_confidence(
    *,
    base: float,
    grounded: bool,
    document_quality: str = "unknown",
    ocr_confidence: float | None = None,
    normalization_confidence: float | None = None,
) -> float:
    if not grounded:
        return 0.0
    quality = {"good": 1.0, "unknown": 0.9, "low": 0.65, "unreadable": 0.2}.get(document_quality, 0.8)
    ocr = 0.8 if ocr_confidence is None else max(0.0, min(1.0, ocr_confidence))
    norm = 1.0 if normalization_confidence is None else max(0.0, min(1.0, normalization_confidence))
    return round(max(0.0, min(1.0, base * quality * (0.7 + 0.3 * ocr) * (0.85 + 0.15 * norm))), 3)
