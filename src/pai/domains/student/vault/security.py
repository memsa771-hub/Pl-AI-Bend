from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SensitiveValueCodec:
    """Fernet-based encoding for sensitive vault payloads (no custom crypto)."""

    def __init__(self, key: str) -> None:
        raw = key.encode() if isinstance(key, str) else key
        self._fernet = Fernet(raw)

    def encrypt_json(self, value: Any) -> str:
        payload = json.dumps(value).encode("utf-8")
        return self._fernet.encrypt(payload).decode("utf-8")

    def decrypt_json(self, token: str) -> Any:
        try:
            data = self._fernet.decrypt(token.encode("utf-8"))
        except InvalidToken as exc:
            raise ValueError("Unable to decode sensitive value.") from exc
        return json.loads(data.decode("utf-8"))


def mask_value(value: Any) -> str:
    return "***"


def generate_fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")
