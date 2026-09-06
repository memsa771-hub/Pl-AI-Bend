from __future__ import annotations

import json

from pai.config import Settings
from pai.intelligences.documents.config import policy, taxonomy
from pai.intelligences.documents.digitization.schemas import DigitizationResult
from pai.intelligences.documents.providers.factory import ocr_provider
from pai.intelligences.documents.providers.native import NativeDocumentProvider
from pai.platform.storage.supabase import SupabaseStorageProvider


async def digitize_bytes(
    data: bytes,
    *,
    mime_type: str,
    filename: str,
    settings: Settings,
    storage: SupabaseStorageProvider | None = None,
    artifact_path: str | None = None,
) -> DigitizationResult:
    native = await NativeDocumentProvider().digitize(data, mime_type=mime_type, filename=filename)
    min_chars = int(policy()["min_text_chars"])
    if len(native.text.strip()) >= min_chars:
        return native
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime not in set(taxonomy()["ocr_mimes"]):
        return native
    provider = ocr_provider(settings)
    if provider.name == "native" or not provider.configured():
        return native.model_copy(update={"needs_ocr": True})
    result = await provider.digitize(data, mime_type=mime, filename=filename)
    if storage is not None and artifact_path and result.raw_response is not None:
        raw = json.dumps(result.raw_response).encode("utf-8")
        await storage.upload_private(artifact_path, raw, "application/json")
        result.raw_response = None
        result = result.model_copy(update={"raw_response": None})
    return result
