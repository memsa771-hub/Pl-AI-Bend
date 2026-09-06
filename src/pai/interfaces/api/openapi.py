"""OpenAPI / Swagger presentation — DX only; does not weaken auth."""

from __future__ import annotations

from typing import Any

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "health", "description": "Liveness and readiness."},
    {"name": "auth", "description": "Signup, login, session."},
    {"name": "account", "description": "Delete account."},
    {"name": "onboarding", "description": "Starting profile or CV."},
    {"name": "person", "description": "Identity and typed records."},
    {"name": "vault", "description": "Structured student facts."},
    {"name": "chat", "description": "Counselor turn and history."},
    {"name": "documents", "description": "Upload, extract, review."},
    {"name": "goals", "description": "Pursuits and active goal."},
]

API_DESCRIPTION = ""

BEARER_DESCRIPTION = "Paste data.accessToken from login. Do not type Bearer."


def customize_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    info = schema.setdefault("info", {})
    info["description"] = ""
    info.pop("summary", None)

    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})

    for key, value in list(schemes.items()):
        if not isinstance(value, dict):
            continue
        if value.get("type") == "http" and str(value.get("scheme", "")).lower() == "bearer":
            value["bearerFormat"] = "JWT"
            value["description"] = BEARER_DESCRIPTION
            if key != "BearerAuth":
                schemes["BearerAuth"] = value
            break

    schema.pop("security", None)
    return schema
