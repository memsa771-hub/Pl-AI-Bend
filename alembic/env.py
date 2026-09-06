from logging.config import fileConfig
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alembic import context
from sqlalchemy import create_engine, pool

from pai.platform.database.base import Base
import pai.domains.goals.models  # noqa: F401
import pai.domains.student.person.models  # noqa: F401
import pai.domains.conversations.models  # noqa: F401
import pai.domains.documents.models  # noqa: F401
import pai.domains.actions.models  # noqa: F401
import pai.domains.memory.models  # noqa: F401
import pai.domains.journey.models  # noqa: F401
import pai.platform.jobs.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    from pai.config import get_settings

    settings = get_settings()
    # Prefer .env via Settings; allow shell override only when explicitly set after load
    url = (settings.database_url or os.getenv("DATABASE_URL") or "").strip()
    if not url or url.startswith("driver://"):
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env (postgresql+asyncpg://...) and retry."
        )
    url = url.replace("+asyncpg", "+psycopg")
    if ("supabase.co" in url or "pooler.supabase.com" in url) and "sslmode=" not in url:
        url = f"{url}{'&' if '?' in url else '?'}sslmode=require"
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
