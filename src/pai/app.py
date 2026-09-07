from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pai.config import Settings, get_settings
from pai.intelligences.counselor.checkpoint import close_graph_checkpointer, init_graph_checkpointer
from pai.intelligences.counselor.prompts import validate_prompt_templates
from pai.interfaces.api import include_routers
from pai.interfaces.api.openapi import OPENAPI_TAGS, customize_openapi_schema
from pai.interfaces.api.schemas import error, humanize_validation_error, success
from pai.interfaces.workers.documents import document_worker_loop
from pai.interfaces.workers.goals import goal_worker_loop
from pai.interfaces.workers.intelligence import intelligence_worker_loop
from pai.kernel.errors import AuthError
from pai.platform.database.db import warmup_database
from pai.platform.latency import LatencyMiddleware, configure_logging
from pai.platform.llm.gateway import LLMGateway
from pai.platform.security.auth.supabase import SupabaseAuthProvider

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    validate_prompt_templates()
    await init_graph_checkpointer(
        settings.database_url,
        enabled=(
            settings.enable_graph_checkpoint
            and settings.app_env not in ("test", "testing")
        ),
    )
    app.state.llm_gateway = LLMGateway(settings)
    if not getattr(app.state, "_provider_initialized", False):
        provider = SupabaseAuthProvider(settings)
        app.state.auth_provider = provider
        app.state._provider_initialized = True
        app.state._owns_provider = True
    if settings.app_env not in {"test", "testing"}:
        async def _warmup_db() -> None:
            try:
                await warmup_database(settings)
            except Exception:
                logger.warning(
                    "Database warmup failed; first request may be slower.",
                    exc_info=True,
                )

        async def _warmup_auth() -> None:
            try:
                await app.state.auth_provider.health_check()
            except Exception:
                logger.warning(
                    "Auth warmup failed; first login may be slower.",
                    exc_info=True,
                )

        await asyncio.gather(_warmup_db(), _warmup_auth())
    worker_stop = asyncio.Event()
    worker_tasks: list[asyncio.Task] = []
    if settings.run_workers_in_api and settings.enable_document_worker:
        worker_tasks.append(asyncio.create_task(document_worker_loop(settings, worker_stop)))
    if settings.run_workers_in_api and settings.enable_intelligence_worker:
        worker_tasks.append(asyncio.create_task(intelligence_worker_loop(settings, worker_stop)))
    if settings.run_workers_in_api and settings.enable_goal_worker:
        worker_tasks.append(asyncio.create_task(goal_worker_loop(settings, worker_stop)))
    try:
        yield
    finally:
        worker_stop.set()
        for worker_task in worker_tasks:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        gateway = getattr(app.state, "llm_gateway", None)
        if gateway is not None:
            await gateway.aclose()
        if getattr(app.state, "_owns_provider", False):
            await app.state.auth_provider.aclose()
        await close_graph_checkpointer()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    if app_settings.app_env not in {"test", "testing"}:
        configure_logging()
    docs_enabled = app_settings.enable_api_docs

    app = FastAPI(
        title="Placement AI (PAI)",
        description="",
        version="0.2.0",
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        swagger_ui_parameters={
            "persistAuthorization": True,
            "displayRequestDuration": True,
            "tryItOutEnabled": True,
            "docExpansion": "list",
            "defaultModelsExpandDepth": 1,
            "filter": True,
        },
    )
    app.state.settings = app_settings

    def override_get_settings() -> Settings:
        return app.state.settings

    app.dependency_overrides[get_settings] = override_get_settings

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description="",
            routes=app.routes,
            tags=OPENAPI_TAGS,
        )
        app.openapi_schema = customize_openapi_schema(schema)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=app_settings.trusted_hosts)

    @app.exception_handler(AuthError)
    async def auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error(exc.code, exc.message),
            headers={"Retry-After": str(getattr(exc, "retry_after", 5))} if exc.status_code in {429, 503} else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error("VALIDATION_ERROR", humanize_validation_error(exc.errors())),
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error("VALIDATION_ERROR", humanize_validation_error(exc.errors())),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled request error")
        return JSONResponse(
            status_code=500,
            content=error("INTERNAL_ERROR", "Request failed. Try again."),
        )

    @app.get(
        "/health/live",
        tags=["health"],
        summary="Liveness probe",
    )
    async def health_live() -> JSONResponse:
        return JSONResponse(content=success({"status": "live"}))

    @app.get("/", include_in_schema=False)
    async def service_root() -> JSONResponse:
        return JSONResponse(
            content=success(
                {
                    "service": "Placement AI (PAI)",
                    "status": "live",
                    "docs": "/docs" if docs_enabled else None,
                    "readiness": "/health/ready",
                }
            )
        )

    @app.get(
        "/health/ready",
        tags=["health"],
        summary="Readiness probe",
    )
    async def health_ready(request: Request) -> JSONResponse:
        provider: SupabaseAuthProvider = request.app.state.auth_provider
        from pai.platform.operations import readiness
        checks = await readiness(app_settings, provider)
        return JSONResponse(status_code=200 if all(checks.values()) else 503,
                            content=success({"status": "ready" if all(checks.values()) else "not_ready", "checks": checks}))

    from pai.platform.request_limits import RequestLimitsMiddleware
    app.add_middleware(RequestLimitsMiddleware, settings=app_settings)

    app.add_middleware(LatencyMiddleware)
    include_routers(app)
    return app


def create_app_from_env() -> FastAPI:
    return create_app(get_settings())
