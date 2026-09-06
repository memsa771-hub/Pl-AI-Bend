"""Request-correlated stage timings without student content or credentials."""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter

logger = logging.getLogger(__name__)
request_id: ContextVar[str] = ContextVar("latency_request_id", default="background")


def configure_logging() -> None:
    """Make stage timings visible under the default Uvicorn logging setup."""
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)


def record(stage: str, started: float, **labels) -> None:
    logger.info(
        "latency request_id=%s stage=%s duration_ms=%.2f labels=%s",
        request_id.get(),
        stage,
        (perf_counter() - started) * 1000,
        labels,
    )


@contextmanager
def span(stage: str, **labels):
    started = perf_counter()
    try:
        yield
    finally:
        record(stage, started, **labels)


def timed(stage: str):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            with span(stage):
                return await fn(*args, **kwargs)

        return wrapped

    return decorate


class LatencyMiddleware:
    """Pure ASGI middleware: keeps timing context alive through SSE completion."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = perf_counter()
        token = request_id.set(uuid.uuid4().hex)
        scope.setdefault("state", {})["request_started"] = started
        logger.info("latency request_id=%s stage=request_received", request_id.get())

        async def traced_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(
                    (b"x-request-id", request_id.get().encode())
                )
            await send(message)

        try:
            await self.app(scope, receive, traced_send)
        finally:
            record("total_request", started)
            request_id.reset(token)
