from __future__ import annotations

from typing import Any, Self, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

T = TypeVar("T")


class ApiErrorBody(BaseModel):
    code: str = Field(examples=["INVALID_CREDENTIALS"])
    message: str = Field(examples=["Email or password is incorrect."])


class ApiErrorResponse(BaseModel):
    success: bool = False
    error: ApiErrorBody


class ApiSuccessResponse[T](BaseModel):
    success: bool = True
    data: T


def _passwords_must_match(password: str, confirm: str) -> None:
    if password != confirm:
        raise ValueError("Passwords do not match.")


_FIELD_LABELS = {
    "fullName": "Full name",
    "email": "Email",
    "phone": "Phone number",
    "dateOfBirth": "Date of birth",
    "nationality": "Nationality",
    "currentCountry": "Current country",
    "currentCity": "Current city",
    "currentStatus": "Current status",
    "educationLevel": "Education level",
    "institution": "Institution",
    "degree": "Degree",
    "major": "Field of study",
    "primaryGoal": "Primary goal",
    "goalDetail": "Goal detail",
    "gender": "Gender",
    "linkedinUrl": "LinkedIn URL",
    "password": "Password",
    "confirmPassword": "Confirm password",
    "newPassword": "New password",
    "ticket": "Reset ticket",
    "code": "Verification code",
    "accessToken": "Access token",
    "refreshToken": "Refresh token",
}

_MSG_PREFIXES = ("Value error, ", "Assertion failed, ")


def humanize_validation_error(errors: list[Any]) -> str:
    if not errors:
        return "Invalid request."
    first = errors[0]
    loc = [str(part) for part in first.get("loc", []) if part not in ("body", "query", "path")]
    field = loc[-1] if loc else ""
    label = _FIELD_LABELS.get(field, field.replace("_", " ").capitalize() if field else "")
    raw = str(first.get("msg") or "")
    for prefix in _MSG_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    type_name = str(first.get("type") or "")
    lowered = raw.lower()

    if "passwords do not match" in lowered:
        return "Passwords do not match."
    if "email" in type_name or "email address" in lowered or (
        field == "email" and type_name == "value_error"
    ):
        return "Enter a valid email address."
    if type_name == "missing":
        return f"{label or 'This field'} is required."
    if type_name in {"string_too_short", "too_short"}:
        min_length = (first.get("ctx") or {}).get("min_length")
        if field in {"password", "newPassword", "confirmPassword"}:
            return f"Password must be at least {min_length or 8} characters."
        if label:
            return f"{label} is too short."
    if raw:
        return raw[0].upper() + raw[1:] if raw[0].islower() else raw
    if label:
        return f"Invalid value for {label.lower()}."
    return "Invalid request."


class SignupRequest(BaseModel):
    fullName: str = Field(min_length=2, max_length=256, examples=["Ali Khan"])
    email: EmailStr = Field(examples=["user@example.com"])
    password: str = Field(min_length=8, max_length=128, examples=["Str0ngPass#1"])
    confirmPassword: str = Field(min_length=8, max_length=128, examples=["Str0ngPass#1"])

    @field_validator("fullName", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        _passwords_must_match(self.password, self.confirmPassword)
        return self


class LoginRequest(BaseModel):
    email: EmailStr = Field(examples=["user@example.com"])
    password: str = Field(min_length=1, max_length=128, examples=["Str0ngPass#1"])


class EmailOnlyRequest(BaseModel):
    email: EmailStr = Field(examples=["user@example.com"])


class VerificationConfirmRequest(BaseModel):
    code: str = Field(min_length=1, description="Verification token from the email link (`token=` query param).")
    email: EmailStr = Field(examples=["user@example.com"], description="Same email used at signup.")
    verifier: str | None = Field(
        default=None,
        description="Optional PKCE code verifier. Leave empty unless your signup flow uses PKCE.",
    )


class PasswordResetRequest(BaseModel):
    ticket: str = Field(min_length=1, description="Password reset ticket from the email link.")
    newPassword: str = Field(min_length=8, max_length=128, examples=["NewStr0ngPass#1"])
    confirmPassword: str = Field(min_length=8, max_length=128, examples=["NewStr0ngPass#1"])

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        _passwords_must_match(self.newPassword, self.confirmPassword)
        return self


class PasswordChangeRequest(BaseModel):
    newPassword: str = Field(min_length=8, max_length=128, examples=["NewStr0ngPass#1"])
    confirmPassword: str = Field(min_length=8, max_length=128, examples=["NewStr0ngPass#1"])

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        _passwords_must_match(self.newPassword, self.confirmPassword)
        return self


class SessionFromTokensRequest(BaseModel):
    """Tokens from the Supabase email-verification redirect hash (never log these)."""

    accessToken: str = Field(min_length=16)
    refreshToken: str = Field(min_length=8)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str | None = None
    emailVerified: bool
    displayName: str | None = None
    avatarUrl: str | None = None
    roles: list[str] = Field(default_factory=list)
    createdAt: str | None = None


class AuthSessionPublic(BaseModel):
    accessToken: str
    accessTokenExpiresIn: int
    user: UserPublic
    onboardingCompleted: bool = False
    onboardingCompletedAt: str | None = None
    onboardingPath: str | None = None
    nextPath: str = "/onboarding"


class SignupResponseData(BaseModel):
    message: str
    email: str | None = None
    session: AuthSessionPublic | None = None


class MessageData(BaseModel):
    message: str


class LoginResponseData(AuthSessionPublic):
    pass


class MeResponseData(BaseModel):
    user: UserPublic
    onboardingCompleted: bool = False
    onboardingCompletedAt: str | None = None
    onboardingPath: str | None = None
    nextPath: str = "/onboarding"


class HealthData(BaseModel):
    status: str


def success(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error": {"code": code, "message": message}}
