from __future__ import annotations

import re

from pai.config import Settings

TurnKind = str  # PERSONAL_ADVICE | PROFILE_UPDATE | LIVE_RESEARCH | DOCUMENT | ACTION

_GREETING = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|ok(?:ay)?(?:\s+go)?|please continue|"
    r"got it|sure|yep|yes|no|help|help me|what now|continue|go ahead|alright|"
    r"salam|shukriya)"
    r"\s*[!.?]*\s*$",
    re.I,
)
_EXPLAIN_ONLY = re.compile(
    r"^\s*(can you explain|what do you mean|could you clarify|help me understand).*\??\s*$",
    re.I,
)
_SELF_FACT = re.compile(
    r"\b("
    r"i am|i'm|i live|i want|i have|i got|i scored|i completed|i finished|"
    r"i study|i studied|i prefer|i'm from|i am from|my gpa|my cgpa|my budget|"
    r"my name|i moved"
    r")\b",
    re.I,
)
_PROFILE_HINT = re.compile(
    r"\d{2,4}\s*/\s*\d{2,4}"
    r"|additional\s+maths"
    r"|\b(?:gpa|cgpa|emsat)\b"
    r"|\b(pre[\s-]?medical|pre[\s-]?engineering|bscs|a[\s-]?levels?)\b",
    re.I,
)
_ADVICE_LEAD = re.compile(
    r"^\s*(what|which|when|where|how|who|latest|current|can you|could you|please)\b",
    re.I,
)
# Live / official facts — not a test-name catalog.
_LIVE_RESEARCH = re.compile(
    r"\b("
    r"deadline|deadlines|ranking|rankings|tuition|fees?|"
    r"scholarship|scholarships|"
    r"requirement|requirements|eligibility|"
    r"(?:ielts|toefl|gre|gmat) score.*(?:need|required)|"
    r"visa (?:rules|cost|requirements)|"
    r"official|website|portal|program page|"
    r"look(?:\s+it)?\s+up|search|google|"
    r"find (?:me )?(?:the |this |current )|"
    r"dhoond|latest|current (?:year|cycle|deadline|fees?|ranking)"
    r")\b",
    re.I,
)
_DOCUMENT = re.compile(
    r"\b(cv|resume|transcript|attach|upload|pdf|document)\b",
    re.I,
)
_ACTION = re.compile(
    r"\b(remind|task|todo|to-do|checklist|lock in)\b",
    re.I,
)


def _has_profile_signal(text: str) -> bool:
    return bool(_SELF_FACT.search(text) or _PROFILE_HINT.search(text))


def is_greeting(message: str) -> bool:
    """Hi / thanks / ok — not a real counseling turn."""
    text = (message or "").strip()
    return len(text) < 2 or bool(_GREETING.match(text))


def counseling_reply_max_tokens(message: str, default: int) -> int:
    """Greetings stay tiny so DeepSeek cannot spend 15s writing an essay."""
    if is_greeting(message):
        return min(int(default), 96)
    return int(default)


def classify_turn(message: str) -> str:
    """Cheap turn kind. Not a second LLM call."""
    text = (message or "").strip()
    if len(text) < 2 or _GREETING.match(text) or _EXPLAIN_ONLY.match(text):
        return "PERSONAL_ADVICE"
    if _LIVE_RESEARCH.search(text):
        return "LIVE_RESEARCH"
    if _DOCUMENT.search(text) and not _has_profile_signal(text):
        return "DOCUMENT"
    if _ACTION.search(text) and not _has_profile_signal(text):
        return "ACTION"
    asking = "?" in text or bool(_ADVICE_LEAD.match(text))
    if asking and not _has_profile_signal(text):
        return "PERSONAL_ADVICE"
    if _has_profile_signal(text) or _PROFILE_HINT.search(text):
        return "PROFILE_UPDATE"
    return "PERSONAL_ADVICE"


def should_extract_facts(message: str) -> bool:
    """Extract unless the turn is provably trivial.

    Deny-list, not allow-list: a question can still carry the facts a counselor
    must remember ("what scholarships can I get? my mother earns 40k"), and a
    regex cannot tell those apart from a pure advice question. Volume is safe
    here — extraction runs in the deferred intelligence job (never in the reply
    path), the omnibus extractor returns no candidates when there is nothing to
    find, and the kernel gates still decide what may reach the Vault.
    """
    text = (message or "").strip()
    if len(text) < 2:
        return False
    if _GREETING.match(text):
        return False
    if _EXPLAIN_ONLY.match(text):
        return False
    return True


def counselor_web_search_enabled(settings: Settings, message: str | None = None) -> bool:
    """Reserve the tool-decision path for current/external information."""
    if not (settings.enable_counselor_tools and (settings.tavily_api_key or "").strip()):
        return False
    return classify_turn(message or "") == "LIVE_RESEARCH"


def counseling_task(message: str) -> str:
    """Only standalone salutations use Luna; contextual replies keep Terra."""
    if re.fullmatch(r"\s*(?:hi|hello|hey|thanks|thank you|salam|shukriya)[!.?]*\s*", message, re.I):
        return "simple_conversation"
    return "student_conversation"
