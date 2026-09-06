"""MIME sniff + size/extension checks. Claimed type must match bytes."""

from __future__ import annotations

import zipfile
from io import BytesIO

from pai.config import Settings
from pai.kernel.errors import AuthError
from pai.intelligences.documents.config import taxonomy


def sniff_mime(data: bytes, filename: str = "") -> str | None:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:2] == b"PK" and _looks_like_docx(data):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if _looks_like_text(data) or (filename or "").lower().endswith(".txt"):
        return "text/plain"
    return None


def _looks_like_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and any(n.startswith("word/") for n in names)


def _looks_like_text(data: bytes) -> bool:
    sample = data[:2048]
    if not sample:
        return False
    if b"\x00" in sample:
        return False
    try:
        text = sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    printable = sum(ch.isprintable() or ch.isspace() for ch in text)
    return printable / max(len(text), 1) >= 0.85


def validate_upload_bytes(
    filename: str, claimed_type: str, data: bytes, settings: Settings
) -> str:
    tax = taxonomy()
    lower = (filename or "").lower()
    for ext in tax["blocked_extensions"]:
        if lower.endswith(ext):
            raise AuthError(code="INVALID_FILE", message="File type not allowed.", status_code=400)
    if len(data) > settings.document_max_bytes:
        raise AuthError(code="FILE_TOO_LARGE", message="File exceeds size limit.", status_code=413)
    sniffed = sniff_mime(data, filename)
    claimed = (claimed_type or "").split(";")[0].strip().lower()
    allow = tax["upload_mimes"]
    if sniffed is None:
        raise AuthError(
            code="INVALID_FILE",
            message="Could not recognize this file. Upload PDF, DOCX, TXT, JPEG, or PNG.",
            status_code=400,
        )
    if claimed and claimed in allow and claimed != sniffed:
        raise AuthError(
            code="INVALID_FILE",
            message="File contents do not match the declared type.",
            status_code=400,
        )
    if sniffed not in allow:
        raise AuthError(code="INVALID_FILE", message="File type not allowed.", status_code=400)
    if sniffed in {m for m in allow if str(m).startswith("image/")} and not settings.document_allow_image_uploads:
        raise AuthError(
            code="INVALID_FILE",
            message="Image uploads are disabled until an OCR provider is configured.",
            status_code=400,
        )
    return sniffed
