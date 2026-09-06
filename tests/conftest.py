from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt

from pai.config import Settings
from pai.kernel.errors import (
    EmailAlreadyInUseError,
    EmailNotVerifiedError,
    IncorrectPasswordError,
    UserNotFoundError,
)
from pai.platform.security.auth.provider import (
    GenericActionResult,
    ProviderSession,
    ProviderUser,
    SignupResult,
)


class FakeAuthProvider:
    def __init__(self, jwt_secret: str = "test-jwt-secret") -> None:
        self.jwt_secret = jwt_secret
        self.users: dict[str, dict] = {}
        self.sessions: dict[str, ProviderSession] = {}
        self.refresh_to_access: dict[str, str] = {}
        self.logout_calls: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.get_user_calls = 0

    async def signup(
        self, email: str, password: str, full_name: str = ""
    ) -> SignupResult:
        key = email.strip().lower()
        if key in self.users:
            raise EmailAlreadyInUseError()
        user_id = f"user-{len(self.users) + 1}"
        self.users[key] = {
            "id": user_id,
            "email": key,
            "password": password,
            "verified": False,
            "full_name": full_name or None,
            "phone": None,
        }
        return SignupResult(
            session=None,
            message="Account created. Please verify your email to continue.",
        )

    async def login(self, email: str, password: str) -> ProviderSession:
        user = self.users.get(email.strip().lower())
        if not user:
            raise UserNotFoundError()
        if user["password"] != password:
            raise IncorrectPasswordError()
        if not user["verified"]:
            raise EmailNotVerifiedError()
        return self._issue_session(user)

    async def refresh(self, refresh_token: str) -> ProviderSession:
        access = self.refresh_to_access.get(refresh_token)
        if not access:
            from pai.kernel.errors import InvalidTokenError

            raise InvalidTokenError()
        session = self.sessions[access]
        return self._issue_session(
            {
                "id": session.user.id,
                "email": session.user.email,
                "verified": session.user.email_verified,
            }
        )

    async def logout(self, access_token: str, refresh_token: str) -> None:
        self.logout_calls.append((access_token, refresh_token))
        self.refresh_to_access.pop(refresh_token, None)

    async def resend_verification(self, email: str) -> GenericActionResult:
        if email.strip().lower() not in self.users:
            raise UserNotFoundError()
        return GenericActionResult(message=f"Verification email has been sent to {email}.")

    async def confirm_verification(
        self,
        code: str,
        verifier: str | None,
        email: str,
    ) -> ProviderSession:
        resolved_email = code.replace("ticket:", "")
        user = self.users.get(resolved_email) or self.users.get(email)
        if not user:
            from pai.kernel.errors import InvalidTokenError

            raise InvalidTokenError()
        user["verified"] = True
        return self._issue_session(user)

    async def request_password_reset(self, email: str) -> GenericActionResult:
        if email.strip().lower() not in self.users:
            raise UserNotFoundError()
        return GenericActionResult(
            message=f"A password recovery email has been sent to {email}."
        )

    async def reset_password(self, ticket: str, new_password: str) -> GenericActionResult:
        email = ticket.replace("passwordReset:", "")
        if email not in self.users:
            from pai.kernel.errors import InvalidTokenError

            raise InvalidTokenError()
        self.users[email]["password"] = new_password
        return GenericActionResult(message="Password has been reset successfully.")

    async def change_password(self, access_token: str, new_password: str) -> GenericActionResult:
        session = self.sessions.get(access_token)
        if not session:
            from pai.kernel.errors import InvalidTokenError

            raise InvalidTokenError()
        for user in self.users.values():
            if user["id"] == session.user.id:
                user["password"] = new_password
        return GenericActionResult(message="Password changed successfully. Please sign in again.")

    async def get_user(self, access_token: str) -> ProviderUser:
        self.get_user_calls += 1
        session = self.sessions.get(access_token)
        if not session:
            from pai.kernel.errors import InvalidTokenError

            raise InvalidTokenError()
        return session.user

    async def delete_user(self, access_token: str) -> None:
        session = self.sessions.get(access_token)
        if not session:
            from pai.kernel.errors import InvalidTokenError

            raise InvalidTokenError()
        self.deleted.append(session.user.id)
        email = session.user.email
        if email:
            self.users.pop(email, None)

    async def health_check(self) -> bool:
        return True

    def _issue_session(self, user: dict) -> ProviderSession:
        access = jwt.encode(
            {
                "sub": user["id"],
                "role": "authenticated",
                "aud": "authenticated",
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            self.jwt_secret,
            algorithm="HS256",
        )
        refresh = f"refresh-{user['id']}-{len(self.sessions)}"
        provider_user = ProviderUser(
            id=user["id"],
            email=user["email"],
            email_verified=user["verified"],
            display_name=user.get("full_name"),
            phone=user.get("phone"),
            roles=["user"],
            created_at=datetime.now(UTC).isoformat(),
        )
        session = ProviderSession(
            access_token=access,
            access_token_expires_in=900,
            refresh_token=refresh,
            user=provider_user,
        )
        self.sessions[access] = session
        self.refresh_to_access[refresh] = access
        return session

    def verify_user(self, email: str) -> None:
        self.users[email]["verified"] = True


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    import os

    db_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://pai:pai@127.0.0.1:5433/pai_auth",
    )
    ssl_verify = "supabase" not in db_url and "pooler.supabase.com" not in db_url
    return Settings(
        _env_file=None,
        LLM_DEFAULT_PROVIDER="deepseek",
        OPENAI_API_KEY="",
        APP_ENV="test",
        CORS_ORIGINS="http://localhost:3000",
        TRUSTED_HOSTS="testserver,localhost",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_ANON_KEY="test-anon-key",
        SUPABASE_SERVICE_ROLE_KEY="test-service-key",
        SUPABASE_JWT_SECRET="test-jwt-secret",
        EMAIL_VERIFICATION_REDIRECT_URL="http://localhost:3000/auth/verify-email",
        PASSWORD_RESET_REDIRECT_URL="http://localhost:3000/auth/reset-password",
        COOKIE_SECURE=False,
        DATABASE_URL=db_url,
        DATABASE_SSL_VERIFY=ssl_verify,
        VAULT_ENCRYPTION_KEY=os.getenv(
            "VAULT_ENCRYPTION_KEY",
            "nAiPKgHP0wblQhCFnmH_2hRsQts1BmOKdHQUa2m0FzQ=",
        ),
        ENABLE_DOCUMENT_WORKER=False,
        ENABLE_INTELLIGENCE_WORKER=False,
        ENABLE_GOAL_WORKER=False,
        DEEPSEEK_API_KEY="",
        TAVILY_API_KEY="",
    )


@pytest.fixture
def fake_provider(test_settings: Settings) -> FakeAuthProvider:
    return FakeAuthProvider(jwt_secret=test_settings.supabase_jwt_secret)


@pytest.fixture
def bearer_token(test_settings: Settings) -> str:
    payload = {
        "sub": "user-1",
        "role": "authenticated",
        "aud": "authenticated",
        "exp": datetime.now(UTC) + timedelta(hours=1),
    }
    return jwt.encode(payload, test_settings.supabase_jwt_secret, algorithm="HS256")


@pytest.fixture
def client(test_settings: Settings, fake_provider: FakeAuthProvider):
    from fastapi.testclient import TestClient

    from pai.app import create_app

    app = create_app(test_settings)
    app.state.auth_provider = fake_provider
    app.state._provider_initialized = True
    with TestClient(app) as test_client:
        yield test_client


def _run_migrations(database_url: str) -> None:
    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


async def _ping_db(settings: Settings) -> None:
    from sqlalchemy import text

    from pai.platform.database.db import get_engine, reset_engine_for_tests

    reset_engine_for_tests()
    engine = get_engine(settings)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _truncate_all(settings: Settings) -> None:
    from sqlalchemy import text

    from pai.platform.database.db import get_session_factory, reset_engine_for_tests

    reset_engine_for_tests()
    factory = get_session_factory(settings)
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE student_tasks, person_semantic_memories, person_events, person_decisions, conversations, messages, orchestration_runs, document_candidates, "
                "verification_cases, document_facts, document_parties, document_analysis_runs, document_relations, "
                "message_documents, document_jobs, document_versions, documents, person_jobs, goal_jobs, goal_intelligence, persons, person_vaults, educations, work_experiences, "
                "projects, skills, certifications, goals, vault_values, vault_evidence, "
                "vault_history, person_consents RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


@pytest.fixture(scope="session")
def postgres_ready(test_settings: Settings) -> Settings:
    import asyncio

    try:
        asyncio.run(_ping_db(test_settings))
    except Exception as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    try:
        _run_migrations(test_settings.database_url)
    except Exception as exc:
        pytest.skip(f"Alembic migrations failed: {exc}")
    return test_settings


@pytest.fixture
def vault_client(postgres_ready: Settings, fake_provider: FakeAuthProvider):
    import asyncio

    from fastapi.testclient import TestClient

    from pai.app import create_app
    from pai.platform.database.db import reset_engine_for_tests

    reset_engine_for_tests()
    asyncio.run(_truncate_all(postgres_ready))
    app = create_app(postgres_ready)
    app.state.auth_provider = fake_provider
    app.state._provider_initialized = True
    with TestClient(app) as test_client:
        yield test_client
    reset_engine_for_tests()


def auth_headers(client, email: str, password: str) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


ONBOARDING_PAYLOAD = {
    "path": "manual",
    "phone": "+923001234567",
    "dateOfBirth": "2004-03-12",
    "nationality": "PK",
    "currentCountry": "PK",
    "currentCity": "Lahore",
    "currentStatus": "student",
    "gender": "male",
    "educationLevel": "bachelor",
    "institution": "Bahria University",
    "degree": "BSCS",
    "major": "computer_science",
    "gpa": 3.4,
    "primaryGoal": "admission",
    "goalDetail": "MS Computer Science in Germany",
    "studyCountry": "DE",
    "intake": "fall",
    "intakeYear": 2027,
    "budget": "limited",
}


def complete_onboarding(client, headers: dict[str, str]) -> None:
    done = client.post("/api/v1/onboarding", headers=headers, json=ONBOARDING_PAYLOAD)
    assert done.status_code == 200, done.text
    data = done.json()["data"]
    assert data["onboardingCompleted"] is True


@pytest.fixture
def verified_user(vault_client, fake_provider: FakeAuthProvider):
    email = "vault-user@example.com"
    fake_provider.users[email] = {
        "id": "vault-user-1",
        "email": email,
        "password": "Password123!",
        "verified": True,
    }
    headers = auth_headers(vault_client, email, "Password123!")
    return vault_client, headers, email


@pytest.fixture
def onboarded_user(verified_user):
    client, headers, email = verified_user
    complete_onboarding(client, headers)
    return client, headers, email
