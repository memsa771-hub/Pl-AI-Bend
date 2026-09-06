"""Read scans/photos with OpenAI vision. Digital PDF/DOCX still use the native parser first."""

from __future__ import annotations

import base64
import logging

import httpx

from pai.config import Settings
from pai.intelligences.documents.config import policy
from pai.intelligences.documents.digitization.schemas import DigitizationResult

logger = logging.getLogger(__name__)

_IMAGE_MIMES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
_RENDER_SCALE = 2
_MIN_JPEG_BYTES = 2000
_PROMPT = (
    "Transcribe this student document verbatim. Keep names, dates, numbers, GPA, "
    "scores, and table rows exactly as written. Do not invent missing fields. "
    "If multiple images are attached, prefix each page with ===PAGE n=== using the "
    "page numbers given in the instruction. Return plain text only."
)


def _page_marker(page: int) -> str:
    return f"===PAGE {page}==="


class OpenAIVisionProvider:
    name = "openai_vision"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def configured(self) -> bool:
        return bool(self._settings.openai_api_key)

    async def digitize(self, data: bytes, *, mime_type: str, filename: str) -> DigitizationResult:
        _ = filename
        model = self._settings.llm_document_vision_model
        if not self.configured():
            return DigitizationResult(
                text="",
                method="unavailable",
                provider=self.name,
                model=model,
                quality="unreadable",
                needs_ocr=True,
            )
        max_pages = max(1, int(self._settings.document_vision_max_pages))
        batch = max(1, int(self._settings.document_vision_batch_pages))
        images, total_pages = pages_for_vision(data, mime_type, max_pages=max_pages)
        if not images:
            return DigitizationResult(
                text="",
                method="unavailable",
                provider=self.name,
                model=model,
                quality="unreadable",
                needs_ocr=True,
                page_count=total_pages,
                truncated=total_pages > max_pages,
            )
        truncated = total_pages > len(images)
        page_rows: list[dict] = []
        timeout = max(
            self._settings.document_processing_timeout_seconds,
            self._settings.llm_timeout_seconds,
        )
        headers = {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        usage: dict = {}
        async with httpx.AsyncClient(timeout=timeout) as client:
            for start in range(0, len(images), batch):
                chunk = images[start : start + batch]
                text, batch_usage = await _transcribe_batch(
                    client,
                    base_url=self._settings.openai_base_url,
                    headers=headers,
                    model=model,
                    max_tokens=int(self._settings.document_vision_max_tokens),
                    images=chunk,
                )
                if text is None:
                    return DigitizationResult(
                        text="",
                        method="unavailable",
                        provider=self.name,
                        model=model,
                        quality="unreadable",
                        needs_ocr=True,
                        page_count=total_pages,
                        truncated=truncated,
                    )
                _merge_usage(usage, batch_usage)
                parts = _split_pages(text, [page for page, _, _ in chunk])
                page_rows.extend(parts)
        min_chars = int(policy()["min_text_chars"])
        chunks: list[str] = []
        for row in page_rows:
            body = str(row.get("text") or "").strip()
            if not body:
                continue
            page = row.get("page")
            chunks.append(f"{_page_marker(int(page))}\n{body}" if page is not None else body)
        full = "\n\n".join(chunks).strip()
        return DigitizationResult(
            text=full,
            method="vision",
            provider=self.name,
            model=model,
            page_count=total_pages,
            quality="good" if len(full) >= min_chars else "low",
            needs_ocr=False,
            truncated=truncated,
            pages=page_rows,
            raw_response={
                "model": model,
                "pages": len(page_rows),
                "total_pages": total_pages,
                "truncated": truncated,
                "usage": usage,
                "text": full,
            },
        )


async def _transcribe_batch(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    model: str,
    max_tokens: int,
    images: list[tuple[int, str, bytes]],
) -> tuple[str | None, dict]:
    pages = [str(page) for page, _, _ in images]
    content: list[dict] = [
        {
            "type": "text",
            "text": f"{_PROMPT}\nPages in this request, in order: {', '.join(pages)}.",
        }
    ]
    for _page, mime, blob in images:
        b64 = base64.b64encode(blob).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
            }
        )
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "max_tokens": max_tokens,
            },
            headers=headers,
        )
    except httpx.HTTPError:
        logger.warning("OpenAI vision request failed")
        return None, {}
    if response.status_code >= 400:
        logger.warning("OpenAI vision error status=%s body=%s", response.status_code, response.text[:300])
        return None, {}
    body = response.json()
    text = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    return text, dict(body.get("usage") or {})


def pages_for_vision(
    data: bytes, mime_type: str, *, max_pages: int
) -> tuple[list[tuple[int, str, bytes]], int]:
    mime = (mime_type or "").split(";")[0].strip().lower()
    mapped = _IMAGE_MIMES.get(mime)
    if mapped:
        return [(1, mapped, data)], 1
    if mime == "application/pdf":
        return _rasterize_pdf(data, max_pages=max_pages)
    return [], 0


def _rasterize_pdf(data: bytes, *, max_pages: int) -> tuple[list[tuple[int, str, bytes]], int]:
    import fitz

    try:
        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return [], 0
    total = len(pdf)
    out: list[tuple[int, str, bytes]] = []
    matrix = fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE)
    for index in range(min(total, max_pages)):
        try:
            pix = pdf[index].get_pixmap(matrix=matrix, alpha=False)
            jpeg = pix.tobytes("jpeg")
        except Exception:
            continue
        if len(jpeg) < _MIN_JPEG_BYTES:
            continue
        out.append((index + 1, "image/jpeg", jpeg))
    pdf.close()
    return out, total


def _merge_usage(total: dict, batch: dict) -> None:
    for key, value in batch.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total[key] = total.get(key, 0) + value


def _split_pages(text: str, page_numbers: list[int]) -> list[dict]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(page_numbers) == 1:
        return [{"page": page_numbers[0], "text": cleaned}]
    if not all(_page_marker(page) in text for page in page_numbers):
        return [{"page": None, "text": cleaned}]
    rows: list[dict] = []
    remaining = text
    for i, page in enumerate(page_numbers):
        marker = _page_marker(page)
        nxt = page_numbers[i + 1] if i + 1 < len(page_numbers) else None
        start = remaining.find(marker)
        chunk = remaining[start + len(marker) :]
        remaining = chunk
        if nxt is not None:
            end = chunk.find(_page_marker(nxt))
            piece, remaining = chunk[:end], chunk[end:]
        else:
            piece = chunk
        rows.append({"page": page, "text": piece.strip()})
    return rows
