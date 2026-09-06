"""The student must never receive the model's deliberation.

A reasoning-capable model sometimes emits its scratchpad as the message. Sending
it leaks the counselor's internal steering — counselor_focus, decision_signal,
gaps, and the prompt's own rules — to the student. These cases are taken from
the real transcript: the LEAKS actually reached students, and the SAFE replies
are genuine counselor messages that must keep working.
"""

from __future__ import annotations

import pytest

from pai.intelligences.counselor.counselor_graph import looks_like_reasoning, public_reply

# Verbatim from production — these were sent to a student.
LEAKS = [
    'The student says "hi I want admission in ms" — they want admission in MS '
    "(CS in Germany). We've been discussing funding/scholarships. The profile "
    "has gaps: target universities.",
    'The student now says "I want MS CS in Germany" — this is a switch from '
    'Columbia (US) to Germany. Per rules: "If they switch fields, accept it."',
    "I have good facts now. Let me synthesize: the rule says \"Never re-ask facts "
    'in the profile block." The gaps are not facts.',
    "Now, the student wants MS CS in Germany. Per the guidance sequence, we've "
    "understood the goal.",
    "The counselor_focus says understand this goal before planning, explore WHY.",
    "The decision_signal notes possible peer pressure.",
]

# Also verbatim from production — legitimate replies that must NOT be dropped.
SAFE = [
    "Musawir, I hear you — you've been going back and forth between medicine and "
    "CS. You'll need financial proof (~€11,904/year in a blocked account) for the "
    "student visa — this is a real cost to plan for.",
    "I found the real facts, and they're actually more workable than you might "
    "fear. Family reunification is usually only considered if the student is in "
    "possession of a residence permit — meaning you'd typically go first.",
    "Okay, I've got real, current facts for you. Let me lay out what the MS path "
    "actually looks like so you can decide.",
    "I have verified facts now: DAAD master's stipend is €992/month.",
    "That's a clear direction, and Germany is genuinely strong for CS. What "
    "pulled you toward Germany specifically?",
    "Hi! Great to hear from you — nice to have a clear goal already.",
]


@pytest.mark.parametrize("text", LEAKS)
def test_reasoning_is_detected(text):
    assert looks_like_reasoning(text), f"leak not caught: {text[:60]}"


@pytest.mark.parametrize("text", SAFE)
def test_real_replies_are_not_flagged(text):
    """False positives are worse than the bug — they blank a working reply."""
    assert not looks_like_reasoning(text), f"false positive: {text[:60]}"


@pytest.mark.parametrize("text", LEAKS)
def test_public_reply_suppresses_reasoning(text):
    assert public_reply(text) == ""


@pytest.mark.parametrize("text", SAFE)
def test_public_reply_passes_real_replies(text):
    assert public_reply(text) == text.strip()


def test_student_visa_phrasing_is_not_a_leak():
    """'the student visa' is a noun phrase, not third-person narration."""
    assert not looks_like_reasoning("You'll need proof for the student visa.")
    assert not looks_like_reasoning("if the student is in possession of a permit")


def test_third_person_narration_is_a_leak():
    assert looks_like_reasoning("The student wants an MS.")
    assert looks_like_reasoning("The student asked about funding.")


def test_reasoning_inside_a_json_envelope_is_suppressed():
    """A parsed envelope is not a free pass.

    The counselor sometimes wraps its output as {"reply": "..."}. Reasoning
    inside that reply is still reasoning, so the envelope branch must go
    through the same filter as bare prose.
    """
    envelope = '{"reply": "The student says \\"hi\\" — they want MS. Per rules: ask one."}'
    assert public_reply(envelope) == ""


def test_real_reply_inside_a_json_envelope_still_passes():
    envelope = '{"reply": "Germany is a strong choice for CS. What draws you there?"}'
    assert public_reply(envelope) == "Germany is a strong choice for CS. What draws you there?"


def test_capitalised_rule_quoting_is_caught():
    """Neighbouring alternatives accept either case; this one must too."""
    assert looks_like_reasoning("Per the rules, I should ask one question.")
    assert looks_like_reasoning("per the rules, I should ask one question.")


async def _drain(deltas):
    """Run _yield_student_text against a fake stream."""
    from pai.intelligences.counselor import counselor_graph as cg

    class _Gateway:
        async def stream(self, **_kw):
            for d in deltas:
                yield d

    return "".join([c async for c in cg._yield_student_text(_Gateway(), [], 640)])


async def test_streamed_reasoning_is_suppressed_before_release():
    """Once a token is sent it cannot be unsent, so the opening is buffered.

    orchestrator.py claimed pre-stream guards existed here; they did not.
    """
    leak = (
        'The student says "hi I want admission in ms" — they want MS in CS in '
        "Germany. Per rules: ask at most one question. The profile has gaps: "
        "target universities, so I should guide toward concrete next steps."
    )
    assert await _drain([leak[i : i + 20] for i in range(0, len(leak), 20)]) == ""


async def test_streamed_real_reply_passes_through_whole():
    reply = (
        "Germany is a genuinely strong choice for CS, and your background fits. "
        "Before we look at universities, what pulled you toward Germany "
        "specifically — the cost, the tech scene, or staying in Europe?"
    )
    assert await _drain([reply[i : i + 20] for i in range(0, len(reply), 20)]) == reply


async def test_short_reply_shorter_than_the_buffer_is_not_swallowed():
    """A greeting reply ends before the guard window fills."""
    assert await _drain(["Hi! ", "Good to ", "hear from you."]) == "Hi! Good to hear from you."


def test_existing_json_filtering_still_works():
    assert public_reply('{"reply": "hello"}') == "hello"
    assert public_reply("```json\n{}\n```") == ""
    assert public_reply("") == ""
    assert public_reply(None) == ""
