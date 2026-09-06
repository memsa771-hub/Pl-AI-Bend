"""Policy-only routing. Semantic decisions come from TurnUnderstanding."""
from pai.config import Settings

TurnKind = str

def is_greeting(message: str) -> bool:
    # No vocabulary classifier: even a one-character answer may carry context.
    return not bool((message or "").strip())

def classify_turn(message: str, understanding=None) -> str:
    return getattr(understanding, "turn_kind", "PERSONAL_ADVICE")

def should_extract_facts(message: str) -> bool:
    return bool((message or "").strip())

def counseling_reply_max_tokens(message: str, default: int) -> int:
    return int(default)

def counselor_web_search_enabled(settings: Settings, message: str | None = None,
                                 understanding=None) -> bool:
    available = bool(settings.enable_counselor_tools and (settings.tavily_api_key or "").strip())
    need = getattr(understanding, "needs_research", None)
    # Unknown must not silently deny the counselor access to evidence.
    return available and need is not False

def counseling_task(message: str) -> str:
    return "student_conversation"
