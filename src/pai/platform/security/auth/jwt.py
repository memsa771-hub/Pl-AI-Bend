"""Access-token verification for Supabase (HS256 legacy + ES256/RS256 JWKS)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
from jose import JWTError, jwk, jwt

from pai.config import Settings
from pai.kernel.errors import InvalidTokenError

logger = logging.getLogger(__name__)

_jwks_lock = threading.Lock()
_jwks_cache: dict[str, Any] = {"fetched_at": 0.0, "keys": []}
_JWKS_TTL_SECONDS = 600


def _jwks_url(settings: Settings) -> str:
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _supabase_http_get(url: str, headers: dict[str, str]) -> httpx.Response:
    # ponytail: IPv4 bind — same Windows AAAA stall as login (~5s).
    with httpx.Client(
        timeout=httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0),
        transport=httpx.HTTPTransport(local_address="0.0.0.0"),
    ) as client:
        return client.get(url, headers=headers)


def _fetch_jwks(settings: Settings) -> list[dict[str, Any]]:
    global _jwks_cache
    now = time.time()
    with _jwks_lock:
        if _jwks_cache["keys"] and (now - float(_jwks_cache["fetched_at"])) < _JWKS_TTL_SECONDS:
            return list(_jwks_cache["keys"])

    try:
        response = _supabase_http_get(
            _jwks_url(settings),
            {"apikey": settings.supabase_anon_key},
        )
        response.raise_for_status()
        keys = list((response.json() or {}).get("keys") or [])
    except Exception as exc:
        logger.warning("Failed to fetch Supabase JWKS: %s", exc)
        with _jwks_lock:
            if _jwks_cache["keys"]:
                return list(_jwks_cache["keys"])
        raise InvalidTokenError(
            "Could not verify token (JWKS unavailable). Retry in a moment."
        ) from exc

    with _jwks_lock:
        _jwks_cache = {"fetched_at": now, "keys": keys}
    return keys


def _key_for_token(token: str, settings: Settings) -> Any:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise InvalidTokenError("Malformed access token.") from exc

    alg = str(header.get("alg") or "HS256")
    kid = header.get("kid")

    if alg == "HS256":
        return settings.supabase_jwt_secret, ["HS256"]

    keys = _fetch_jwks(settings)
    if not keys:
        raise InvalidTokenError(
            "Token uses asymmetric signing but project JWKS has no keys."
        )

    matching = [k for k in keys if not kid or k.get("kid") == kid]
    if not matching:
        # Force refresh once if kid unknown (rotation).
        with _jwks_lock:
            _jwks_cache["fetched_at"] = 0.0
        keys = _fetch_jwks(settings)
        matching = [k for k in keys if not kid or k.get("kid") == kid]
    if not matching:
        raise InvalidTokenError("No matching JWKS signing key for this token.")

    try:
        return jwk.construct(matching[0]), [alg]
    except Exception as exc:
        raise InvalidTokenError("Invalid JWKS signing key material.") from exc


def validate_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify Supabase user JWT (HS256 secret or ES256/RS256 via JWKS)."""
    raw = (token or "").strip()
    if not raw:
        raise InvalidTokenError("Missing access token.")

    key, algorithms = _key_for_token(raw, settings)
    try:
        payload = jwt.decode(
            raw,
            key,
            algorithms=algorithms,
            audience=settings.supabase_jwt_audience,
            options={"verify_aud": True},
        )
    except JWTError as exc:
        # Fallback: some jose/EC combinations are picky — verify with Auth server.
        if algorithms != ["HS256"]:
            payload = _verify_via_supabase_user(raw, settings)
            if payload is not None:
                return payload
        message = str(exc) or "Invalid or expired token."
        lower = message.lower()
        if "expired" in lower:
            raise InvalidTokenError("Access token expired. Login again and re-Authorize.") from exc
        if "audience" in lower:
            raise InvalidTokenError(
                f"Token audience mismatch (expected '{settings.supabase_jwt_audience}')."
            ) from exc
        if "signature" in lower:
            raise InvalidTokenError(
                "Token signature invalid. Re-login and paste only data.accessToken in Authorize."
            ) from exc
        raise InvalidTokenError("Invalid or expired token. Login again and re-Authorize.") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise InvalidTokenError("Token missing subject.")
    role = payload.get("role")
    if role not in (None, "authenticated", "service_role"):
        raise InvalidTokenError("Token role is not allowed.")
    return payload


def _verify_via_supabase_user(token: str, settings: Settings) -> dict[str, Any] | None:
    """Network verification fallback for asymmetric JWTs."""
    try:
        response = _supabase_http_get(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
            {
                "apikey": settings.supabase_anon_key,
                "Authorization": f"Bearer {token}",
            },
        )
        if response.status_code >= 400:
            return None
        data = response.json()
        user_id = data.get("id")
        if not user_id:
            return None
        # Prefer claims from the token for consistency with local path.
        claims = jwt.get_unverified_claims(token)
        if str(claims.get("sub")) != str(user_id):
            return None
        role = claims.get("role")
        if role not in (None, "authenticated", "service_role"):
            return None
        return claims
    except Exception as exc:
        logger.warning("Supabase /user token fallback failed: %s", exc)
        return None


def reset_jwks_cache_for_tests() -> None:
    global _jwks_cache
    with _jwks_lock:
        _jwks_cache = {"fetched_at": 0.0, "keys": []}
