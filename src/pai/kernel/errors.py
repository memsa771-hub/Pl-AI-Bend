from dataclasses import dataclass


@dataclass(slots=True)
class AuthError(Exception):
    code: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


class ProviderUnavailableError(AuthError):
    def __init__(self, message: str = "Authentication service is temporarily unavailable.") -> None:
        super().__init__(code="PROVIDER_UNAVAILABLE", message=message, status_code=503)


class InvalidCredentialsError(AuthError):
    def __init__(self, message: str = "Email or password is incorrect.") -> None:
        super().__init__(code="INVALID_CREDENTIALS", message=message, status_code=401)


class UserNotFoundError(AuthError):
    def __init__(self, message: str = "No account exists with this email.") -> None:
        super().__init__(code="USER_NOT_FOUND", message=message, status_code=404)


class IncorrectPasswordError(AuthError):
    def __init__(self, message: str = "Incorrect password.") -> None:
        super().__init__(code="INCORRECT_PASSWORD", message=message, status_code=401)


class EmailNotVerifiedError(AuthError):
    def __init__(
        self,
        message: str = "Please verify your email before signing in.",
    ) -> None:
        super().__init__(code="EMAIL_NOT_VERIFIED", message=message, status_code=403)


class EmailAlreadyInUseError(AuthError):
    def __init__(
        self, message: str = "An account with this email already exists. Please log in instead."
    ) -> None:
        super().__init__(code="EMAIL_ALREADY_IN_USE", message=message, status_code=409)


class InvalidTokenError(AuthError):
    def __init__(self, message: str = "Invalid or expired token.") -> None:
        super().__init__(code="INVALID_TOKEN", message=message, status_code=401)


class ValidationFailedError(AuthError):
    def __init__(self, message: str) -> None:
        super().__init__(code="VALIDATION_ERROR", message=message, status_code=422)


class ForbiddenError(AuthError):
    def __init__(self, message: str = "You are not allowed to perform this action.") -> None:
        super().__init__(code="FORBIDDEN", message=message, status_code=403)


class CsrfError(AuthError):
    def __init__(self, message: str = "CSRF validation failed.") -> None:
        super().__init__(code="CSRF_FAILED", message=message, status_code=403)


class PersonNotFoundError(AuthError):
    def __init__(self, message: str = "Person profile not found.") -> None:
        super().__init__(code="PERSON_NOT_FOUND", message=message, status_code=404)


class VersionConflictError(AuthError):
    def __init__(self, message: str = "Resource was modified. Refresh and retry.") -> None:
        super().__init__(code="VERSION_CONFLICT", message=message, status_code=409)


class UnknownFieldError(AuthError):
    def __init__(self, message: str = "Unknown vault field.") -> None:
        super().__init__(code="UNKNOWN_FIELD", message=message, status_code=400)


class FieldNotEditableError(AuthError):
    def __init__(self, message: str = "This field cannot be edited directly.") -> None:
        super().__init__(code="FIELD_NOT_EDITABLE", message=message, status_code=400)


class ConsentRequiredError(AuthError):
    def __init__(self, message: str = "Consent required for this sensitive category.") -> None:
        super().__init__(code="CONSENT_REQUIRED", message=message, status_code=403)


class OnboardingIncompleteError(AuthError):
    def __init__(
        self,
        message: str = (
            "Complete onboarding before using PAI. "
            "POST /api/v1/onboarding with the starting profile, or POST /api/v1/onboarding/cv."
        ),
    ) -> None:
        super().__init__(code="ONBOARDING_INCOMPLETE", message=message, status_code=403)
