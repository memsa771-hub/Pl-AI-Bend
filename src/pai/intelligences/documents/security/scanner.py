"""Malware scan hook. Default is a no-op until DOCUMENT_MALWARE_SCAN_PROVIDER is set."""

from __future__ import annotations

from pai.config import Settings
from pai.kernel.errors import AuthError


class ScanResult:
    def __init__(self, *, clean: bool, provider: str, detail: str = "") -> None:
        self.clean = clean
        self.provider = provider
        self.detail = detail


async def scan_bytes(data: bytes, *, filename: str, settings: Settings) -> ScanResult:
    _ = data, filename
    provider = (settings.document_malware_scan_provider or "none").strip().lower()
    if provider in ("", "none"):
        return ScanResult(clean=True, provider="none")
    # Placeholder: wire ClamAV / vendor API here using settings.
    raise AuthError(
        code="SCANNER_NOT_CONFIGURED",
        message="Malware scanner is selected but not implemented. Set DOCUMENT_MALWARE_SCAN_PROVIDER=none.",
        status_code=503,
    )
