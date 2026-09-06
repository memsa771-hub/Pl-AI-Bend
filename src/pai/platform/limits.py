"""Atomic database-backed limits shared by API processes and background workers."""
import asyncio
import hashlib
from contextvars import ContextVar
from datetime import UTC, datetime

from sqlalchemy import text
from pai.kernel.errors import AuthError
from pai.platform.database.db import get_session_factory

usage_subject = ContextVar("usage_subject", default="background")

class LimitExceeded(AuthError):
    def __init__(self, retry_after):
        super().__init__(code="USAGE_LIMIT", message="Usage limit reached. Please try again later.", status_code=429)
        self.retry_after = retry_after

def enabled(settings):
    return settings.enable_rate_limits and settings.app_env not in {"test", "testing"}

async def consume(settings, items):
    """Reserve (namespace, subject, cost, limit, window seconds) atomically.

    Failed provider calls retain reservations to cap retry storms. Token costs
    are conservative upper bounds, not billing totals. No credentials are stored.
    """
    if not enabled(settings):
        return
    try:
        async with asyncio.timeout(2):
            async with get_session_factory(settings)() as session:
                now = float((await session.execute(text("SELECT extract(epoch FROM clock_timestamp())"))).scalar_one())
                for namespace, subject, cost, limit, window in sorted(items, key=lambda item: (item[0], str(item[1]), item[4])):
                    bucket = int(now // window)
                    end = (bucket + 1) * window
                    key = f"{namespace}:{hashlib.sha256(str(subject).encode()).hexdigest()}:{bucket}"
                    if cost > limit:
                        raise LimitExceeded(max(1, int(end - now)))
                    accepted = await session.execute(text(
                        "INSERT INTO usage_counters (key, used, expires_at) VALUES (:key, :cost, :expiry) "
                        "ON CONFLICT (key) DO UPDATE SET used = usage_counters.used + :cost "
                        "WHERE usage_counters.used + :cost <= :limit RETURNING used"),
                        {"key": key, "cost": cost, "expiry": datetime.fromtimestamp(end, UTC), "limit": limit})
                    if accepted.scalar_one_or_none() is None:
                        raise LimitExceeded(max(1, int(end - now)))
                await session.commit()
    except AuthError:
        raise
    except Exception as exc:
        raise AuthError(code="LIMITS_UNAVAILABLE", message="Service temporarily unavailable.", status_code=503) from exc

async def reserve_llm(settings, request, *, subject=None):
    if not enabled(settings):
        return
    # UTF-8 bytes conservatively bound text tokens, including structured inputs.
    import json
    tokens = len(json.dumps(request.model_dump(), ensure_ascii=False, default=str).encode()) + request.max_tokens + (1024 if request.reasoning_effort not in (None, "none") else 0)
    subject = subject or usage_subject.get()
    await consume(settings, [
        ("llm_calls", subject, 1, settings.llm_call_limit_per_day, 86400),
        ("llm_tokens", subject, tokens, settings.llm_token_limit_per_day, 86400),
        ("llm_global", "all", tokens, settings.llm_global_token_limit_per_day, 86400),
    ])
