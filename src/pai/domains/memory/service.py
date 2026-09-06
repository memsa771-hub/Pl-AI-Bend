from __future__ import annotations

import logging
import uuid
from typing import Any

from agentspan.agents.memory import ConversationMemory
from agentspan.agents.semantic_memory import SemanticMemory
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pai.config import Settings
from pai.domains.memory.postgres_store import AsyncPostgresMemoryStore, InProcessMemoryStore

logger = logging.getLogger(__name__)


class PersonMemoryService:
    """Facade: conversation window + formed long-term memory per student.

    Agents may *read* memory via tools; writes of unstructured notes still go
    through remember(). Structured observations are formed (upsert/strengthen)
    by pai.domains.memory.formation — not dumped as one blob per turn.
    Vault remains current structured truth; Journey remains goals.
    """

    def __init__(
        self,
        settings: Settings,
        person_id: uuid.UUID,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        semantic: SemanticMemory | None = None,
        conversation: ConversationMemory | None = None,
    ) -> None:
        self._settings = settings
        self._person_id = person_id
        self._session_factory = session_factory
        self._async_store = (
            AsyncPostgresMemoryStore(session_factory, person_id) if session_factory else None
        )
        # AgentSpan SemanticMemory always gets a sync store for API compatibility;
        # production reads/writes prefer the async Postgres path when available.
        store = InProcessMemoryStore()
        self.semantic = semantic or SemanticMemory(
            store=store,
            max_results=settings.semantic_memory_max_results,
            session_id=str(person_id),
        )
        self.conversation = conversation or ConversationMemory(
            max_messages=settings.conversation_memory_max_messages
        )

    @property
    def person_id(self) -> uuid.UUID:
        return self._person_id

    async def recall(self, query: str, *, top_k: int | None = None, mode: str = "fast") -> str:
        k = top_k or self._settings.semantic_memory_max_results
        if self._async_store is not None:
            entries = await self._async_store.search(query, top_k=k, mode=mode)
            if not entries:
                return ""
            lines = ["Relevant context from memory:"]
            for i, mem in enumerate(entries, 1):
                lines.append(f"  {i}. {mem.content}")
            return "\n".join(lines)
        return self.semantic.get_context(query)

    async def remember(
        self,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        text = (content or "").strip()
        if not text:
            raise ValueError("Memory content must be non-empty.")
        if len(text) > 2000:
            text = text[:2000]
        meta = {"person_id": str(self._person_id), **(metadata or {})}
        if self._async_store is not None:
            from agentspan.agents.semantic_memory import MemoryEntry

            return await self._async_store.add(MemoryEntry(content=text, metadata=meta))
        return self.semantic.add(text, metadata=meta)

    def hydrate_conversation(self, messages: list[dict[str, str]]) -> None:
        self.conversation.clear()
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "user":
                self.conversation.add_user_message(content)
            elif role == "assistant":
                self.conversation.add_assistant_message(content)
            elif role == "system":
                self.conversation.add_system_message(content)

    def record_turn(self, *, user: str, assistant: str) -> None:
        self.conversation.add_user_message(user)
        self.conversation.add_assistant_message(assistant)
