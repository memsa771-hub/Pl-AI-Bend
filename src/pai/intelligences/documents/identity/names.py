"""Unicode-preserving identity comparison; aliases need separate evidence."""
import unicodedata

def fold_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text).split())

def name_tokens(value: str | None) -> set[str]:
    return set(fold_name(value).split())

def names_match(left: str | None, right: str | None) -> str:
    a, b = fold_name(left), fold_name(right)
    if not a or not b:
        return "ambiguous"
    if a == b:
        return "matched"
    # Differing spellings, scripts and order require evidence or student review.
    return "ambiguous"
