from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

logger = logging.getLogger(__name__)

_checkpointer: BaseCheckpointSaver | None = None
_checkpointer_cm: AbstractAsyncContextManager[Any] | None = None


def get_graph_checkpointer() -> BaseCheckpointSaver:
    if _checkpointer is None:
        return MemorySaver()
    return _checkpointer


async def init_graph_checkpointer(database_url: str, *, enabled: bool = True) -> None:
    global _checkpointer, _checkpointer_cm
    if _checkpointer is not None:
        return
    if not enabled:
        _checkpointer = MemorySaver()
        return
    import sys

    if sys.platform == "win32":
        # Async psycopg checkpointer is unreliable on Windows ProactorEventLoop; use memory locally.
        _checkpointer = MemorySaver()
        return
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        conn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        cm = AsyncPostgresSaver.from_conn_string(conn)
        saver = await cm.__aenter__()
        await saver.setup()
        _checkpointer = saver
        _checkpointer_cm = cm
        logger.info("LangGraph Postgres checkpointer initialized.")
    except Exception:
        logger.exception("Postgres checkpointer unavailable; using in-memory saver.")
        _checkpointer = MemorySaver()


async def close_graph_checkpointer() -> None:
    global _checkpointer, _checkpointer_cm
    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
    _checkpointer = None
    _checkpointer_cm = None
