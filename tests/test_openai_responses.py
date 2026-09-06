import json

import httpx
import pytest
from pydantic import BaseModel

from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.provider import LLMProviderError
from pai.platform.llm.providers.openai_responses import OpenAIResponsesProvider
from pai.platform.llm.schemas import LLMMessage, LLMRequest


def completion(output=None, status="completed"):
    return {
        "status": status,
        "model": "gpt-5.6-terra",
        "usage": {"input_tokens": 20},
        "output": output
        or [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hello"}],
            }
        ],
    }


def request():
    return LLMRequest(
        messages=[LLMMessage(role="user", content="Hi")],
        model="gpt-5.6-terra",
        reasoning_effort="low",
        verbosity="low",
        prompt_cache_key="pai:counseling:v1",
    )


async def test_responses_payload_and_tool_continuation(test_settings):
    payloads = []
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "web_search",
            "arguments": '{"query":"deadline"}',
        },
    ]

    def handler(req):
        assert req.url.path == "/v1/responses"
        payloads.append(json.loads(req.content))
        return httpx.Response(200, json=completion(output if len(payloads) == 1 else None))

    provider = OpenAIResponsesProvider(test_settings, transport=httpx.MockTransport(handler))
    try:
        first = request()
        first.tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        result = await provider.generate(first)
        assert result.content == "" and result.tool_calls[0].id == "call_1"
        first.messages.extend(
            [
                LLMMessage(role="assistant", provider_output=result.provider_output),
                LLMMessage(role="tool", tool_call_id="call_1", content="Evidence"),
            ]
        )
        assert (await provider.generate(first)).content == "Hello"
        assert payloads[1]["input"][1:3] == output
        assert payloads[1]["input"][-1]["type"] == "function_call_output"
        assert payloads[0]["reasoning"] == {"effort": "low"}
        assert payloads[0]["text"]["verbosity"] == "low"
        assert payloads[0]["store"] is False
        assert "temperature" not in payloads[0]
        assert payloads[0]["tools"][0]["name"] == "web_search"
        assert payloads[0]["prompt_cache_key"] == "pai:counseling:v1"
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "terminal", ["response.completed", "response.failed", "response.incomplete", "missing"]
)
async def test_stream_only_final_text_and_terminal_validation(test_settings, terminal):
    events = [
        {"type": "response.reasoning_text.delta", "delta": "private reasoning"},
        {"type": "response.function_call_arguments.delta", "delta": "private arguments"},
        {"type": "response.output_text.delta", "delta": "Hello"},
    ]
    if terminal != "missing":
        events.append({"type": terminal, "response": completion()})
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    provider = OpenAIResponsesProvider(
        test_settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=body, headers={"Content-Type": "text/event-stream"})
        ),
    )
    chunks = []
    try:
        if terminal == "response.completed":
            chunks = [part async for part in provider.stream(request())]
        else:
            with pytest.raises(LLMProviderError):
                async for part in provider.stream(request()):
                    chunks.append(part)
        assert chunks == ["Hello"]
    finally:
        await provider.aclose()


class ClosedResult(BaseModel):
    answer: str
    note: str | None = None


class OpenResult(BaseModel):
    values: dict[str, str]


@pytest.mark.parametrize(
    "schema,content,format_type",
    [
        (ClosedResult, '{"answer":"ok","note":null}', "json_schema"),
        (OpenResult, '{"values":{"arbitrary":"preserved"}}', "json_object"),
    ],
)
async def test_structured_formats(test_settings, schema, content, format_type):
    def handler(req):
        payload = json.loads(req.content)
        fmt = payload["text"]["format"]
        assert fmt["type"] == format_type
        if format_type == "json_schema":
            assert fmt["strict"] is True
            assert fmt["schema"]["additionalProperties"] is False
            assert fmt["schema"]["required"] == ["answer", "note"]
        return httpx.Response(
            200,
            json=completion(
                [{"type": "message", "content": [{"type": "output_text", "text": content}]}]
            ),
        )

    provider = OpenAIResponsesProvider(test_settings, transport=httpx.MockTransport(handler))
    try:
        assert isinstance(await provider.generate_structured(request(), schema), schema)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("status", [400, 401, 429, 500])
async def test_provider_errors_are_sanitized(test_settings, status):
    provider = OpenAIResponsesProvider(
        test_settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="sensitive provider body")
        ),
    )
    try:
        with pytest.raises(LLMProviderError, match="OpenAI response request failed"):
            await provider.generate(request())
    finally:
        await provider.aclose()


async def test_gateway_workload_routing(test_settings):
    settings = test_settings.model_copy(update={"llm_default_provider": "openai"})
    gateway = LLMGateway(settings)
    captured = []

    class Provider:
        async def generate(self, req):
            captured.append(req)
            return None

    gateway.register_provider("openai", Provider())
    for task in ("student_conversation", "extraction", "goal_intelligence", "complex_analysis"):
        await gateway.run(task=task, messages=[])
    assert [req.model for req in captured] == [
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    ]
    assert [req.reasoning_effort for req in captured] == ["low", "none", "medium", "medium"]
    assert captured[0].timeout_seconds == 30
