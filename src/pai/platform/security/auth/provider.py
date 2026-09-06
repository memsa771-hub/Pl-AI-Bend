from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class ProviderUser:
    id: str
    email: str | None
    email_verified: bool
    display_name: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    roles: list[str] | None = None
    created_at: str | None = None


@dataclass(slots=True)
class ProviderSession:
    access_token: str
    access_token_expires_in: int
    refresh_token: str
    user: ProviderUser


@dataclass(slots=True)
class SignupResult:
    session: ProviderSession | None
    message: str


@dataclass(slots=True)
class GenericActionResult:
    message: str


class AuthProvider(Protocol):
    async def signup(
        self,
        email: str,
        password: str,
        full_name: str = "",
    ) -> SignupResult: ...

    async def login(self, email: str, password: str) -> ProviderSession: ...

    async def refresh(self, refresh_token: str) -> ProviderSession: ...

    async def logout(self, access_token: str, refresh_token: str) -> None: ...

    async def resend_verification(self, email: str) -> GenericActionResult: ...

    async def confirm_verification(
        self,
        code: str,
        verifier: str | None,
        email: str,
    ) -> ProviderSession: ...

    async def request_password_reset(self, email: str) -> GenericActionResult: ...

    async def reset_password(self, ticket: str, new_password: str) -> GenericActionResult: ...

    async def change_password(
        self, access_token: str, new_password: str
    ) -> GenericActionResult: ...

    async def get_user(self, access_token: str) -> ProviderUser: ...

    async def delete_user(self, access_token: str) -> None: ...
