"""Load Document Intelligence taxonomy and policy from package data (not code)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parent / "data"


def _read(name: str) -> dict[str, Any]:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def taxonomy() -> dict[str, Any]:
    return _read("taxonomy.json")


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    return _read("policy.json")


def reload() -> None:
    taxonomy.cache_clear()
    policy.cache_clear()
