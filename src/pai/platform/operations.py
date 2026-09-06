"""Bounded readiness probes and worker progress persisted across processes."""
import asyncio
from sqlalchemy import text
from pai.platform.database.db import get_session_factory

EXPECTED_MIGRATION = "016_operational_guards"
WORKER_TABLES = {"documents": "document_jobs", "goals": "goal_jobs", "intelligence": "person_jobs"}

async def heartbeat(settings, kind):
    async with get_session_factory(settings)() as session:
        await session.execute(text(
            "INSERT INTO worker_heartbeats(kind,last_seen) VALUES (:kind,now()) "
            "ON CONFLICT(kind) DO UPDATE SET last_seen=now()"), {"kind": kind})
        # Expired windows cannot affect new requests. Keep storage bounded.
        await session.execute(text("DELETE FROM usage_counters WHERE expires_at < now() - interval '1 day'"))
        await session.commit()

async def readiness(settings, provider):
    checks = {"auth": False, "database": False, "migration": False, "workers": False}
    async def auth():
        try:
            checks["auth"] = bool(await provider.health_check())
        except Exception:
            pass
    async def database():
        try:
            async with get_session_factory(settings)() as session:
                await session.execute(text("SELECT 1"))
                checks["database"] = True
                revisions = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
                checks["migration"] = set(revisions) == {EXPECTED_MIGRATION}
                if not checks["migration"]:
                    return
                healthy = True
                flags = {"documents": settings.enable_document_worker,
                         "goals": settings.enable_goal_worker,
                         "intelligence": settings.enable_intelligence_worker}
                for kind, table in WORKER_TABLES.items():
                    if not flags[kind]:
                        continue
                    age = (await session.execute(text(
                        "SELECT extract(epoch FROM now()-last_seen) FROM worker_heartbeats WHERE kind=:kind"),
                        {"kind": kind})).scalar_one_or_none()
                    oldest = (await session.execute(text(
                        f"SELECT max(extract(epoch FROM now()-available_at)) FROM {table} "
                        "WHERE status IN ('pending','processing','running') AND available_at <= now()"))).scalar_one_or_none()
                    healthy &= age is not None and 0 <= age <= settings.worker_heartbeat_max_age_seconds
                    healthy &= oldest is None or oldest <= settings.worker_queue_max_age_seconds
                checks["workers"] = bool(healthy)
        except Exception:
            pass
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            await asyncio.gather(auth(), database())
    except TimeoutError:
        pass
    return checks
