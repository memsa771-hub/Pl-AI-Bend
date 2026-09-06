"""JWT verification: HS256 secret + ES256 JWKS."""

from __future__ import annotations

import pytest
from jose import jwt

from pai.kernel.errors import InvalidTokenError
from pai.platform.security.auth.jwt import reset_jwks_cache_for_tests, validate_access_token


def test_hs256_token_still_validates(test_settings):
    reset_jwks_cache_for_tests()
    token = jwt.encode(
        {
            "sub": "user-1",
            "role": "authenticated",
            "aud": "authenticated",
        },
        test_settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    payload = validate_access_token(token, test_settings)
    assert payload["sub"] == "user-1"


def test_invalid_token_raises(test_settings):
    reset_jwks_cache_for_tests()
    with pytest.raises(InvalidTokenError):
        validate_access_token("not-a-jwt", test_settings)
