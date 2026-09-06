import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import ValidationError

from pai.config import Settings
from pai.kernel.errors import (
    EmailAlreadyInUseError,
    IncorrectPasswordError,
    ProviderUnavailableError,
    UserNotFoundError,
)
from pai.platform.security.auth.supabase import SupabaseAuthProvider


@pytest.fixture
def supabase_settings() -> Settings:
    return Settings(
        SUPABASE_URL="https://project.supabase.co",
        SUPABASE_ANON_KEY="anon-key",
        SUPABASE_SERVICE_ROLE_KEY="service-key",
        SUPABASE_JWT_SECRET="jwt-secret",
        EMAIL_VERIFICATION_REDIRECT_URL="http://localhost:3000/verify",
        PASSWORD_RESET_REDIRECT_URL="http://localhost:3000/reset",
        CORS_ORIGINS="http://localhost:3000",
        TRUSTED_HOSTS="testserver",
        DATABASE_URL="postgresql+asyncpg://pai:pai@127.0.0.1:5433/pai_auth",
        VAULT_ENCRYPTION_KEY="nAiPKgHP0wblQhCFnmH_2hRsQts1BmOKdHQUa2m0FzQ=",
    )


@pytest.mark.asyncio
async def test_supabase_login_unknown_email(supabase_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Invalid login credentials",
                },
            )
        if request.url.path.endswith("/admin/users"):
            return httpx.Response(200, json={"users": []})
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = SupabaseAuthProvider(supabase_settings, client=client)

    with pytest.raises(UserNotFoundError):
        await provider.login("a@example.com", "wrong")

    await provider.aclose()


@pytest.mark.asyncio
async def test_supabase_login_incorrect_password(supabase_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "Invalid login credentials",
                },
            )
        if request.url.path.endswith("/admin/users"):
            return httpx.Response(
                200,
                json={"users": [{"id": "u1", "email": "a@example.com"}]},
            )
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = SupabaseAuthProvider(supabase_settings, client=client)

    with pytest.raises(IncorrectPasswordError):
        await provider.login("a@example.com", "wrong")

    await provider.aclose()


@pytest.mark.asyncio
async def test_supabase_timeout_raises_provider_unavailable(supabase_settings: Settings):
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = SupabaseAuthProvider(supabase_settings, client=client)

    with pytest.raises(ProviderUnavailableError):
        await provider.login("a@example.com", "Password123!")

    await provider.aclose()


@pytest.mark.asyncio
async def test_supabase_signup_without_session(supabase_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/admin/users"):
            return httpx.Response(200, json={"users": []})
        assert request.url.path.endswith("/signup")
        query = parse_qs(urlparse(str(request.url)).query)
        assert query["redirect_to"] == ["http://localhost:3000/verify"]
        body = json.loads(request.content)
        assert body["options"]["email_redirect_to"] == "http://localhost:3000/verify"
        assert body["data"]["full_name"] == "Ali Khan"
        assert "phone" not in body["data"]
        return httpx.Response(
            200,
            json={
                "id": "user-id",
                "email": "new@example.com",
                "email_confirmed_at": None,
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = SupabaseAuthProvider(supabase_settings, client=client)

    result = await provider.signup("new@example.com", "Password123!", "Ali Khan")
    assert result.session is None

    await provider.aclose()


@pytest.mark.asyncio
async def test_supabase_signup_rejects_existing_email(supabase_settings: Settings):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/admin/users"):
            return httpx.Response(
                200,
                json={"users": [{"id": "u1", "email": "taken@example.com"}]},
            )
        return httpx.Response(500, json={"message": "signup should not be called"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    provider = SupabaseAuthProvider(supabase_settings, client=client)

    with pytest.raises(EmailAlreadyInUseError):
        await provider.signup("taken@example.com", "Password123!", "Ali Khan")

    await provider.aclose()


def _settings_kwargs(**overrides) -> dict:
    data = {
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_ANON_KEY": "anon-key",
        "SUPABASE_SERVICE_ROLE_KEY": "service-key",
        "SUPABASE_JWT_SECRET": "jwt-secret",
        "EMAIL_VERIFICATION_REDIRECT_URL": "http://localhost:3000/auth/verify-email",
        "PASSWORD_RESET_REDIRECT_URL": "http://localhost:3000/auth/reset-password",
        "CORS_ORIGINS": "http://localhost:3000",
        "TRUSTED_HOSTS": "testserver",
        "DATABASE_URL": "postgresql+asyncpg://pai:pai@127.0.0.1:5433/pai_auth",
        "VAULT_ENCRYPTION_KEY": "nAiPKgHP0wblQhCFnmH_2hRsQts1BmOKdHQUa2m0FzQ=",
    }
    data.update(overrides)
    return data


def test_redirect_url_cannot_be_site_root():
    with pytest.raises(ValidationError):
        Settings(**_settings_kwargs(EMAIL_VERIFICATION_REDIRECT_URL="http://localhost:3000"))


def test_redirect_origin_must_be_in_cors():
    with pytest.raises(ValidationError):
        Settings(
            **_settings_kwargs(
                EMAIL_VERIFICATION_REDIRECT_URL="http://localhost:3001/auth/verify-email"
            )
        )
