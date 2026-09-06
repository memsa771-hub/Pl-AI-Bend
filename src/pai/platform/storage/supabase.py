from __future__ import annotations

import uuid

import httpx

from pai.config import Settings
from pai.kernel.errors import AuthError


class StorageAccessError(AuthError):
    def __init__(self) -> None:
        super().__init__(
            code="STORAGE_ACCESS_DENIED",
            message="Storage path is not owned by this person.",
            status_code=403,
        )


class SupabaseStorageProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = f"{settings.supabase_url.rstrip('/')}/storage/v1"
        self._client = httpx.AsyncClient(
            timeout=60.0,
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.supabase_service_role_key}",
            "apikey": self._settings.supabase_service_role_key,
        }

    async def upload_private(self, path: str, data: bytes, content_type: str) -> str:
        bucket = self._settings.supabase_storage_bucket
        url = f"{self._base}/object/{bucket}/{path}"
        response = await self._client.post(
            url,
            content=data,
            headers={**self._headers(), "Content-Type": content_type, "x-upsert": "false"},
        )
        if response.status_code >= 400:
            raise RuntimeError("Storage upload failed.")
        return path

    async def create_signed_download_url(
        self,
        path: str,
        *,
        person_id: uuid.UUID,
        expires_seconds: int = 900,
    ) -> str:
        expected_prefix = f"{person_id}/"
        if not path.startswith(expected_prefix):
            raise StorageAccessError()
        bucket = self._settings.supabase_storage_bucket
        url = f"{self._base}/object/sign/{bucket}/{path}"
        response = await self._client.post(
            url,
            json={"expiresIn": expires_seconds},
            headers=self._headers(),
        )
        if response.status_code >= 400:
            raise RuntimeError("Signed URL failed.")
        data = response.json()
        signed = data.get("signedURL") or data.get("signedUrl") or ""
        if signed.startswith("/"):
            return f"{self._settings.supabase_url.rstrip('/')}/storage/v1{signed}"
        return signed

    async def delete_object(self, path: str) -> None:
        bucket = self._settings.supabase_storage_bucket
        url = f"{self._base}/object/{bucket}/{path}"
        response = await self._client.delete(url, headers=self._headers())
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError("Storage delete failed.")

    async def download_bytes(self, path: str) -> bytes:
        bucket = self._settings.supabase_storage_bucket
        url = f"{self._base}/object/{bucket}/{path}"
        response = await self._client.get(url, headers=self._headers())
        if response.status_code >= 400:
            raise RuntimeError("Storage download failed.")
        return response.content
