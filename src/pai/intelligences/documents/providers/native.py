from __future__ import annotations

from pai.intelligences.documents.config import policy, taxonomy
from pai.intelligences.documents.digitization.schemas import DigitizationResult
from pai.domains.documents.text import MAX_CHARS, extract_text_from_bytes, pdf_page_texts


class NativeDocumentProvider:
    name = "native"

    def configured(self) -> bool:
        return True

    async def digitize(self, data: bytes, *, mime_type: str, filename: str) -> DigitizationResult:
        mime = (mime_type or "").split(";")[0].strip().lower()
        native_mimes = set(taxonomy()["native_parse_mimes"])
        if mime not in native_mimes:
            return DigitizationResult(
                text="",
                method="unavailable",
                provider=self.name,
                quality="unreadable",
                needs_ocr=True,
            )
        pages: list[dict] = []
        if mime == "application/pdf" or (filename or "").lower().endswith(".pdf"):
            page_texts = pdf_page_texts(data)
            pages = [{"page": i + 1, "text": part} for i, part in enumerate(page_texts)]
            text = "\n\n".join(part for part in page_texts if part).strip()[:MAX_CHARS]
        else:
            text = extract_text_from_bytes(data, mime, filename)
            if text.strip():
                pages = [{"page": 1, "text": text}]
        quality = "good" if len(text.strip()) >= int(policy()["min_text_chars"]) else "unreadable"
        return DigitizationResult(
            text=text,
            method="native_text",
            provider=self.name,
            page_count=len(pages) or 1,
            quality=quality,
            needs_ocr=quality == "unreadable" and mime == "application/pdf",
            pages=pages,
        )
