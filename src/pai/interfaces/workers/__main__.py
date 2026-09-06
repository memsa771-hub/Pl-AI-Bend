"""Run one worker per process: python -m pai.interfaces.workers goals."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from pai.config import get_settings
from pai.interfaces.workers import (
    document_worker_loop,
    goal_worker_loop,
    intelligence_worker_loop,
)
from pai.platform.database.db import get_engine

WORKERS = {
    "documents": document_worker_loop,
    "goals": goal_worker_loop,
    "intelligence": intelligence_worker_loop,
}


async def run_worker(kind: str) -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    task = asyncio.create_task(WORKERS[kind](settings, stop))
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((task, stopping), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task  # Surface startup/runtime failures to the process supervisor.
        else:
            try:
                await asyncio.wait_for(task, timeout=30)
            except TimeoutError:
                pass
    finally:
        stop.set()
        task.cancel()
        stopping.cancel()
        await asyncio.gather(task, stopping, return_exceptions=True)
        await get_engine(settings).dispose()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=WORKERS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(args.kind))


if __name__ == "__main__":
    main()
