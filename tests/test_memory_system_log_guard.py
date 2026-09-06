"""Memory holds facts about the student, never our own telemetry.

An earlier pipeline wrote audit lines ("Vault accepted: …", "VaultIntel[chat]
extracted …") through the unstructured remember() path. They stayed live and
rankable, so they competed for recall slots and put internal vocabulary into
the counselor's context — one of them ranked first for "how can I afford this?".
"""

from __future__ import annotations

import pytest

from pai.intelligences.counselor.memory_tools import (
    RememberInsightTool,
    is_system_log_text,
)
from pai.intelligences.counselor.tooling import ToolContext

# Verbatim from the rows found in the database.
SYSTEM_LOGS = [
    "Vault accepted: application.career_interest (confidence=0.95)",
    "Vault accepted: application.study_country (confidence=0.95)",
    "VaultIntel[chat] extracted application.career_interest, application.study_country",
    "Extraction completed for person 9f7d64a1",
    "Orchestration run finished",
]

# Genuine insights the counselor should be free to store.
REAL_INSIGHTS = [
    "Student is anxious about telling their parents they switched from medicine to CS.",
    "Prefers tuition-free public universities over prestige.",
    "Mentioned the family cannot fund more than one application cycle.",
    "Wants to stay in Europe to be near a sibling.",
    # Near-miss: mentions the vault as a topic, not as a log line.
    "Student asked what we store in their vault.",
]


@pytest.mark.parametrize("text", SYSTEM_LOGS)
def test_system_logs_are_detected(text):
    assert is_system_log_text(text)


@pytest.mark.parametrize("text", REAL_INSIGHTS)
def test_real_insights_are_not_flagged(text):
    """False positives silently drop genuine student context."""
    assert not is_system_log_text(text)


def test_guard_only_matches_at_the_start():
    """A student sentence that merely contains the words must still store."""
    assert not is_system_log_text(
        "They asked whether the vault accepted their transcript upload."
    )


def test_empty_text_is_not_a_system_log():
    assert not is_system_log_text("")
    assert not is_system_log_text(None)


class _Memory:
    def __init__(self):
        self.stored: list[str] = []

    async def remember(self, content, *, metadata=None):
        self.stored.append(content)
        return "mem-1"


def _ctx(memory):
    return ToolContext(
        settings=None, memory=memory, person_id="p1", conversation_id="c1"
    )


async def test_remember_tool_rejects_system_logs_without_storing():
    memory = _Memory()
    result = await RememberInsightTool().ainvoke(
        {"insight": "Vault accepted: application.study_country (confidence=0.95)"},
        _ctx(memory),
    )
    assert result.ok is False
    assert memory.stored == [], "system log must never reach the store"


async def test_remember_tool_stores_a_real_insight():
    memory = _Memory()
    result = await RememberInsightTool().ainvoke(
        {"insight": "Student is the first in their family to apply abroad."},
        _ctx(memory),
    )
    assert result.ok is True
    assert memory.stored == ["Student is the first in their family to apply abroad."]


async def test_remember_tool_still_rejects_empty_insight():
    memory = _Memory()
    result = await RememberInsightTool().ainvoke({"insight": "  "}, _ctx(memory))
    assert result.ok is False
    assert memory.stored == []
