"""Parse DeepSeek-style SSE chat.completion chunks into text deltas."""

from __future__ import annotations

import json


def delta_from_sse_line(line: str) -> str | None:
    raw = (line or "").strip()
    if not raw.startswith("data:"):
        return None
    payload = raw[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        chunk = json.loads(payload)
    except json.JSONDecodeError:
        return None
    choices = chunk.get("choices") or []
    if not choices:
        return None
    delta = (choices[0] or {}).get("delta") or {}
    text = delta.get("content")
    return text if isinstance(text, str) and text else None
