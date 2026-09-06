"""Goal intelligence must survive a model that does not return bare JSON.

The counselor model wraps JSON in fences, leads with a sentence, or gets cut off
by max_tokens. A bare json.loads on the whole response raised on all three,
which showed up as "LLM JSON call failed / Expecting value: line 1 column 1" and
silently dropped the stage's result.
"""

from __future__ import annotations

from pai.intelligences.goals.pipeline import _extract_json_object, _first_json_object


def test_bare_json():
    assert _extract_json_object('{"requirements": ["a"]}') == {"requirements": ["a"]}


def test_fenced_json():
    assert _extract_json_object('```json\n{"options": ["TUM"]}\n```') == {"options": ["TUM"]}


def test_fenced_without_language_tag():
    assert _extract_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_single_line_fenced_json():
    """The fence and the JSON on one line.

    Stripping the fence by dropping the first line emptied the string, and the
    balanced-brace fallback then scanned the emptied text — reintroducing the
    silent {} this function exists to prevent.
    """
    assert _extract_json_object('```json {"a": 1}```') == {"a": 1}
    assert _extract_json_object('```{"a": 1}```') == {"a": 1}


def test_unterminated_fence():
    assert _extract_json_object('```json\n{"a": 1}') == {"a": 1}


def test_prose_preamble_before_json():
    """The failure seen in production: a sentence, then the JSON."""
    raw = 'Here is the structured data you asked for:\n\n{"deadlines": ["Jan 15"]}'
    assert _extract_json_object(raw) == {"deadlines": ["Jan 15"]}


def test_prose_after_json():
    raw = '{"typical_cost": "unknown"}\n\nLet me know if you need more detail.'
    assert _extract_json_object(raw) == {"typical_cost": "unknown"}


def test_braces_inside_strings_do_not_confuse_the_scanner():
    raw = '{"notes": "costs vary {a lot} by state", "typical_cost": "unknown"}'
    assert _extract_json_object(raw) == {
        "notes": "costs vary {a lot} by state",
        "typical_cost": "unknown",
    }


def test_nested_objects():
    raw = 'Result:\n{"outer": {"inner": {"deep": 1}}, "n": 2}'
    assert _extract_json_object(raw) == {"outer": {"inner": {"deep": 1}}, "n": 2}


def test_truncated_json_returns_none():
    """Cut off by max_tokens — unrecoverable, must not raise."""
    assert _extract_json_object('{"requirements": ["a", "b"') is None


def test_empty_and_prose_only_return_none():
    for raw in ("", "   ", "I cannot help with that request."):
        assert _extract_json_object(raw) is None


def test_json_array_is_rejected():
    """Callers index by key; a top-level list would break them downstream."""
    assert _extract_json_object('["a", "b"]') is None


def test_first_json_object_finds_nothing_without_braces():
    assert _first_json_object("no json here") is None
