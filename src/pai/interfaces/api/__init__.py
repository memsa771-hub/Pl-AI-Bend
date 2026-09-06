"""HTTP routers. Paths and auth dependencies are unchanged."""

from fastapi import FastAPI

from pai.interfaces.api.auth import account_router
from pai.interfaces.api.auth import router as auth_router
from pai.interfaces.api.chat import chat_router
from pai.interfaces.api.documents import router as documents_router
from pai.interfaces.api.goals import goals_router
from pai.interfaces.api.onboarding import router as onboarding_router
from pai.interfaces.api.person import router as person_router
from pai.interfaces.api.vault import router as vault_router


def include_routers(app: FastAPI) -> None:
    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(person_router)
    app.include_router(onboarding_router)
    app.include_router(vault_router)
    app.include_router(chat_router)
    app.include_router(documents_router)
    app.include_router(goals_router)
