"""Long-term semantic + session conversation memory (AgentSpan-backed)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pai.domains.memory.service import PersonMemoryService

__all__ = ["PersonMemoryService"]


def __getattr__(name: str):
    if name == "PersonMemoryService":
        from pai.domains.memory.service import PersonMemoryService as _PersonMemoryService

        return _PersonMemoryService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
