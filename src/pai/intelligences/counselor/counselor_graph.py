from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from pai.config import Settings
from pai.domains.memory.service import PersonMemoryService
from pai.intelligences.counselor.prompts import render_template
from pai.intelligences.counselor.registry import ToolRegistry, build_default_registry
from pai.intelligences.counselor.routing import counseling_reply_max_tokens, counseling_task
from pai.intelligences.counselor.tooling import ToolContext
from pai.kernel.contracts.schemas import ConversationResult
from pai.platform.latency import timed
from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.schemas import LLMMessage, LLMResponse, LLMToolCall, LLMToolCallFunction

logger = logging.getLogger(__name__)

# Characters buffered before the stream is released. Long enough for a leak to
# announce itself, short enough not to feel like a stall.
_STREAM_GUARD_CHARS = 180

# Shown when filtering leaves nothing. A blank bubble reads as a broken app and
# titles the conversation "".
NO_REPLY_FALLBACK = "I couldn't generate a reply just now. Please send that again."


def counselor_seed_messages(prompt_vars: dict[str, Any]) -> list[dict[str, Any]]:
    """Static system + stable profile + real turns. Cache-friendly prefix."""
    system = render_template("system.v1.jinja2")
    profile = render_template(
        "student_conversation.v1.jinja2",
        profile_block=prompt_vars.get("profile_block") or "(no stored profile yet)",
    )
    current = (prompt_vars.get("current_message") or "").strip()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "system", "content": profile},
    ]
    for turn in prompt_vars.get("recent_turns") or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if role == "user" and content == current:
            continue
        # A leaked reasoning turn already in the transcript is replayed as an
        # example of our own voice and teaches the model to keep narrating.
        # Drop it from the prompt; the stored row is cleaned separately.
        if role == "assistant" and looks_like_reasoning(content):
            logger.warning("Dropping leaked reasoning from replayed history")
            continue
        messages.append({"role": role, "content": content})
    extra = (prompt_vars.get("web_note") or "").strip()
    user_text = current if not extra else f"{extra}\n\n{current}"
    messages.append({"role": "user", "content": user_text})
    return messages


async def iter_counselor_tokens(
    *,
    gateway: LLMGateway,
    settings: Settings,
    prompt_vars: dict[str, Any],
    registry: ToolRegistry,
    tool_ctx: ToolContext,
    enable_tools: bool,
) -> AsyncIterator[str]:
    """Yield student-visible prose tokens. Tools run before the streamed reply."""
    raw_messages = counselor_seed_messages(prompt_vars)
    tools = registry.openai_tools() if enable_tools else []
    max_rounds = max(1, int(settings.counselor_max_tool_rounds))
    max_tokens = counseling_reply_max_tokens(
        str(prompt_vars.get("current_message") or ""),
        settings.llm_counseling_max_tokens,
    )
    if not tools:
        task = counseling_task(str(prompt_vars.get("current_message") or ""))
        async for delta in _yield_student_text(gateway, raw_messages, max_tokens, task=task):
            yield delta
        return
    for round_n in range(max_rounds):
        llm = [_dict_to_llm_message(m) for m in raw_messages]
        response = await timed("tool_decision")(gateway.run)(
            task="student_conversation",
            messages=llm,
            temperature=0.3,
            tools=tools,
            tool_choice="auto",
            max_tokens=max_tokens,
        )
        assert isinstance(response, LLMResponse)
        if response.has_tool_calls:
            assistant_msg = {
                "role": "assistant",
                "provider_output": response.provider_output,
                "content": response.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            raw_messages.append(assistant_msg)
            pairs = await asyncio.gather(
                *[_run_tool(registry, tool_ctx, tc) for tc in assistant_msg["tool_calls"]]
            )
            raw_messages.extend(item[0] for item in pairs)
            continue
        parsed = _result_from_text(response.content or "")
        # A parsed envelope is not exempt: reasoning inside {"reply": "..."} is
        # still reasoning, so both branches go through public_reply().
        text = (
            public_reply(parsed.reply)
            if parsed is not None
            else public_reply(response.content)
        )
        if text:
            yield text
            return
        break
    async for delta in _yield_student_text(gateway, raw_messages, max_tokens):
        yield delta


async def _yield_student_text(
    gateway: LLMGateway, raw_messages: list[dict[str, Any]], max_tokens: int,
    *, task: str = "student_conversation",
) -> AsyncIterator[str]:
    """Stream prose; if the stream is empty, one non-stream completion."""
    llm = [_dict_to_llm_message(m) for m in raw_messages]
    got = False
    head: list[str] = []
    releasing = False
    async for delta in gateway.stream(
        task=task, messages=llm, max_tokens=max_tokens
    ):
        if not delta:
            continue
        got = True
        if releasing:
            yield delta
            continue
        # Hold the opening until there is enough to judge: reasoning announces
        # itself early ("The student says…"), and once a token is sent it
        # cannot be unsent. Nothing downstream can filter a live stream.
        head.append(delta)
        if sum(len(part) for part in head) < _STREAM_GUARD_CHARS:
            continue
        opening = "".join(head)
        if looks_like_reasoning(opening):
            logger.warning("Counselor streamed reasoning; suppressed (%d chars)", len(opening))
            return
        releasing = True
        yield opening
    if got:
        if not releasing and head:
            # Reply ended before filling the buffer.
            opening = "".join(head)
            if looks_like_reasoning(opening):
                logger.warning("Counselor streamed reasoning; suppressed (%d chars)", len(opening))
                return
            yield opening
        return
    response = await gateway.run(
        task=task, messages=llm, max_tokens=max_tokens
    )
    assert isinstance(response, LLMResponse)
    parsed = _result_from_text(response.content or "")
    # A parsed envelope is not exempt: reasoning inside {"reply": "..."} is
    # still reasoning, so both branches go through public_reply().
    text = public_reply(parsed.reply) if parsed is not None else public_reply(response.content)
    if not text:
        # Last-resort raw content still goes through the same guards: an empty
        # public_reply() means the output was JSON or reasoning, and neither is
        # safe to show. Falling back to a generic line beats leaking internals.
        candidate = (response.content or "").strip()
        if (
            candidate
            and not candidate.startswith("{")
            and "```" not in candidate
            and not looks_like_reasoning(candidate)
        ):
            text = candidate
    if text:
        yield text
        return
    yield NO_REPLY_FALLBACK


@timed("tool_execution")
async def _run_tool(
    registry: ToolRegistry, tool_ctx: ToolContext, raw_tc: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    tc = _normalize_tool_call(raw_tc)
    result = await registry.execute(tc.function.name, tc.function.arguments, tool_ctx)
    return (
        {
            "role": "tool",
            "tool_call_id": tc.id,
            "name": tc.function.name,
            "content": result.content,
        },
        {"tool": tc.function.name, "ok": result.ok, "preview": result.content[:240]},
    )


async def run_counselor_with_tools(
    *,
    gateway: LLMGateway,
    settings: Settings,
    memory: PersonMemoryService,
    prompt_vars: dict[str, Any],
    person_id: str,
    conversation_id: str,
    registry: ToolRegistry | None = None,
    enable_tools: bool | None = None,
) -> tuple[ConversationResult, list[dict[str, Any]]]:
    registry = registry or build_default_registry()
    tool_ctx = ToolContext(
        settings=settings,
        memory=memory,
        person_id=person_id,
        conversation_id=conversation_id,
    )
    tools_on = (
        enable_tools if enable_tools is not None else settings.enable_counselor_tools
    )
    chunks: list[str] = []
    async for delta in iter_counselor_tokens(
        gateway=gateway,
        settings=settings,
        prompt_vars=prompt_vars,
        registry=registry,
        tool_ctx=tool_ctx,
        enable_tools=bool(tools_on and registry.openai_tools()),
    ):
        chunks.append(delta)
    text = "".join(chunks)
    parsed = _result_from_text(text)
    # Rebuilding from raw text would restore exactly what public_reply removed.
    result = parsed if parsed is not None else ConversationResult(reply=public_reply(text))
    return result, []


def _normalize_tool_call(raw: dict[str, Any] | LLMToolCall) -> LLMToolCall:
    if isinstance(raw, LLMToolCall):
        return raw
    fn = raw.get("function") or {}
    args = fn.get("arguments", "{}")
    if not isinstance(args, str):
        args = json.dumps(args)
    return LLMToolCall(
        id=str(raw.get("id") or "tool_call"),
        function=LLMToolCallFunction(name=str(fn.get("name") or ""), arguments=args),
    )


def _dict_to_llm_message(raw: dict[str, Any]) -> LLMMessage:
    tool_calls = None
    if raw.get("tool_calls"):
        tool_calls = [_normalize_tool_call(tc) for tc in raw["tool_calls"]]
    return LLMMessage(
        role=raw["role"],
        content=raw.get("content"),
        name=raw.get("name"),
        tool_call_id=raw.get("tool_call_id"),
        tool_calls=tool_calls,
        provider_output=raw.get("provider_output"),
    )


def _first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i, char in enumerate(text[start:], start):
        if in_str:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_conversation_json(text: str) -> ConversationResult | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    blob = _first_json_object(raw)
    if not blob:
        return None
    try:
        parsed = ConversationResult.model_validate(json.loads(blob))
    except Exception:
        return None
    reply = (parsed.reply or "").strip()
    if not reply or reply.startswith("{") or "```" in reply:
        return None
    parsed.reply = reply
    return parsed


# Deliberation the student must never see. Only phrases that cannot occur when
# speaking TO a student: third-person narration about them, quoting our own
# instructions, or naming internal prompt fields. Validated against the stored
# assistant transcript — these fire on real leaks and on nothing legitimate.
# ("the student visa", "if the student is in possession of…" must NOT match,
# hence the verb requirement after "the student".)
_REASONING_LEAK = re.compile(
    r"\b[Tt]he student (?:says|said|wants|now|asked|is asking|has been|switched)\b"
    r"|\b[Pp]er rules\b|\b[Pp]er the rules\b|\b[Pp]er the guidance\b"
    r"|\bcounselor_focus\b|\bdecision_signal\b"
    r"|\b[Tt]he rule says\b"
)


def looks_like_reasoning(text: str | None) -> bool:
    """True when the model narrated its deliberation instead of replying.

    A reasoning-capable model occasionally emits its scratchpad as the message.
    Shipping that leaks the counselor's internal steering (focus, gaps, pressure
    heuristics, prompt rules) to the student, so it is dropped rather than sent.
    """
    return bool(_REASONING_LEAK.search(text or ""))


def public_reply(text: str | None) -> str:
    """Student-visible channel: prose only.

    Envelope JSON is never the message, and neither is the model's reasoning.
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    if looks_like_reasoning(raw):
        logger.warning(
            "Counselor emitted reasoning instead of a reply; suppressed (%d chars)",
            len(raw),
        )
        return ""
    if raw.startswith("{") or "```" in raw:
        parsed = _parse_conversation_json(raw)
        if parsed is not None:
            return parsed.reply
        blob = _first_json_object(raw)
        if blob:
            try:
                data = json.loads(blob)
            except Exception:
                data = None
            if isinstance(data, dict) and isinstance(data.get("reply"), str):
                inner = data["reply"].strip()
                if inner and not inner.startswith("{") and "```" not in inner:
                    return inner
        return ""
    return raw


def _result_from_text(content: str) -> ConversationResult | None:
    """Reuse model output only when it is already a valid student reply or schema JSON."""
    text = (content or "").strip()
    if not text:
        return None
    parsed = _parse_conversation_json(text)
    if parsed is not None:
        return parsed
    reply = public_reply(text)
    if not reply:
        return None
    return ConversationResult(reply=reply)
