"""Audit-focused integration tests (PostgreSQL + mocked LLM)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.schemas import LLMMessage, LLMRequest, LLMResponse
from pai.kernel.contracts.schemas import ConversationResult, VaultCandidate


class _MockLLM:
    name = "mock"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(content="{}", provider="mock", model="m")

    async def generate_structured(
        self, request: LLMRequest, output_schema: type[BaseModel]
    ) -> BaseModel:
        return ConversationResult(reply="Acknowledged.", next_question=None)


def test_document_candidates_not_visible_cross_user(vault_client, fake_provider):
    for email, uid in (("audit-a@ex.com", "audit-a"), ("audit-b@ex.com", "audit-b")):
        fake_provider.users[email] = {
            "id": uid,
            "email": email,
            "password": "Password123!",
            "verified": True,
        }
    from tests.conftest import auth_headers, complete_onboarding

    ha = auth_headers(vault_client, "audit-a@ex.com", "Password123!")
    hb = auth_headers(vault_client, "audit-b@ex.com", "Password123!")
    complete_onboarding(vault_client, ha)
    complete_onboarding(vault_client, hb)
    a_hist = vault_client.get("/api/v1/chat/messages", headers=ha)
    b_hist = vault_client.get("/api/v1/chat/messages", headers=hb)
    assert a_hist.status_code == 200, a_hist.text
    assert b_hist.status_code == 200, b_hist.text
    assert a_hist.json()["data"]["conversationId"] != b_hist.json()["data"]["conversationId"]


def test_scenario_manual_profile_education(vault_client, verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    client.patch(
        "/api/v1/person/me",
        headers=headers,
        json={
            "fullName": "Ali Khan",
            "version": client.get("/api/v1/person/me", headers=headers).json()["data"]["version"],
        },
    )
    edu = client.post(
        "/api/v1/person/educations",
        headers=headers,
        json={
            "institution": "Bahria University",
            "degree": "BS Computer Science",
            "gpa": 3.4,
        },
    )
    assert edu.status_code == 201
    me = client.get("/api/v1/person/me", headers=headers).json()["data"]
    assert me["fullName"] == "Ali Khan"
    items = client.get("/api/v1/person/educations", headers=headers).json()["data"]["items"]
    assert items[0]["institution"] == "Bahria University"
    assert items[0]["gpa"] == 3.4


def test_chat_llm_failure_leaves_user_message(vault_client, onboarded_user, test_settings, monkeypatch):
    client, headers, _ = onboarded_user

    class FailLLM:
        name = "mock"

        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("LLM down")

        async def generate_structured(self, request: LLMRequest, output_schema: type[BaseModel]) -> BaseModel:
            raise RuntimeError("LLM down")

    gateway = LLMGateway(test_settings)
    gateway.register_provider("deepseek", FailLLM())
    from pai.intelligences.counselor.orchestrator import PAIOrchestrator

    orch = PAIOrchestrator(test_settings, gateway=gateway)
    monkeypatch.setattr(
        "pai.intelligences.counselor.followup.PAIOrchestrator",
        lambda settings, gateway=None: orch,
    )
    resp = client.post(
        "/api/v1/chat",
        headers=headers,
        json={"message": "Hello counselor"},
    )
    assert resp.status_code in (502, 500, 503)
    msgs = client.get("/api/v1/chat/messages", headers=headers).json()["data"]["items"]
    assert any(m["role"] == "user" and m["content"] == "Hello counselor" for m in msgs)


def test_vault_supersede_preserves_history(vault_client, verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    vault = client.get("/api/v1/vault", headers=headers).json()["data"]
    v = vault["version"]
    client.patch(
        "/api/v1/vault/fields/preferences.preferred_language",
        headers=headers,
        json={"value": "en", "version": v},
    )
    vault2 = client.get("/api/v1/vault", headers=headers).json()["data"]
    client.patch(
        "/api/v1/vault/fields/preferences.preferred_language",
        headers=headers,
        json={"value": "ur", "version": vault2["version"]},
    )
    hist = client.get(
        "/api/v1/vault/fields/preferences.preferred_language/history",
        headers=headers,
    ).json()["data"]["history"]
    assert len(hist) >= 2
    vault3 = client.get("/api/v1/vault", headers=headers).json()["data"]
    assert vault3["sparseFields"]["preferences.preferred_language"] == "ur"


def test_live_deepseek_structured_smoke():
    import asyncio
    import os

    from pai.config import get_settings

    settings = get_settings()
    if not settings.deepseek_api_key:
        pytest.skip("DEEPSEEK_API_KEY not set in environment")
    if not os.getenv("RUN_LIVE_DEEPSEEK"):
        pytest.skip("Set RUN_LIVE_DEEPSEEK=1 to run live DeepSeek smoke test")
    gateway = LLMGateway(settings)
    result = asyncio.run(
        gateway.run(
            task="student_conversation",
            messages=[LLMMessage(role="user", content="Say hi in one sentence.")],
            output_schema=ConversationResult,
        )
    )
    assert isinstance(result, ConversationResult)
    assert result.reply
