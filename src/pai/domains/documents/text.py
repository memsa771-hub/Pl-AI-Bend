"""Pull plain text from uploaded CV/documents. Empty string means unreadable."""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

MAX_CHARS = 50_000
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_text_from_bytes(data: bytes, mime_type: str, filename: str) -> str:
    lower = (filename or "").lower()
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime.startswith("text/") or lower.endswith(".txt"):
        return data.decode("utf-8", errors="replace")[:MAX_CHARS]
    if mime == "application/pdf" or lower.endswith(".pdf"):
        # ponytail: scanned PDFs have no text layer; OpenAI vision rasterizes pages later.
        return _pdf_text(data)[:MAX_CHARS]
    if (
        mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        or lower.endswith(".docx")
    ):
        return _docx_text(data)[:MAX_CHARS]
    return ""


def pdf_page_texts(data: bytes) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return []
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            return []
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            parts.append("")
    return parts


def _pdf_text(data: bytes) -> str:
    return "\n\n".join(part for part in pdf_page_texts(data) if part).strip()


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml")
    except Exception:
        return ""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    paras: list[str] = []
    for para in root.iter(f"{_W_NS}p"):
        runs = [node.text or "" for node in para.iter(f"{_W_NS}t")]
        line = "".join(runs).strip()
        if line:
            paras.append(line)
    return "\n".join(paras).strip()
