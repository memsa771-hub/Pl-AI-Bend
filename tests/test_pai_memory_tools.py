from __future__ import annotations

import uuid

import pytest

from pai.domains.memory.service import PersonMemoryService
from pai.intelligences.counselor.prompts import validate_prompt_templates
from pai.intelligences.counselor.tooling import ToolContext
from pai.intelligences.counselor.registry import build_default_registry
from pai.intelligences.counselor.web_search import WebSearchTool


def test_prompt_templates_validate():
    validate_prompt_templates()


@pytest.mark.asyncio
async def test_web_search_degrades_without_api_key(test_settings):
    settings = test_settings.model_copy(update={"tavily_api_key": ""})
    memory = PersonMemoryService(settings, uuid.uuid4())
    ctx = ToolContext(
        settings=settings,
        memory=memory,
        person_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
    )
    tool = WebSearchTool()
    result = await tool.ainvoke({"query": "TU Munich MS CS deadline"}, ctx)
    assert result.ok is False
    assert "TAVILY_API_KEY" in result.content


@pytest.mark.asyncio
async def test_semantic_memory_roundtrip(test_settings):
    memory = PersonMemoryService(test_settings, uuid.uuid4())
    mid = await memory.remember(
        "Prefers evening study plans and low-cost cities in Germany.",
        metadata={"type": "preference"},
    )
    assert mid
    ctx = await memory.recall("Germany budget study")
    assert "Germany" in ctx or "evening" in ctx or "low-cost" in ctx


@pytest.mark.asyncio
async def test_tool_registry_lists_openai_schemas(test_settings):
    registry = build_default_registry()
    schemas = registry.openai_tools()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"web_search", "recall_semantic_memory", "remember_insight"}
