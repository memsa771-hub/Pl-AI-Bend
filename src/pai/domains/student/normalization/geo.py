"""ISO 3166-1 countries via pycountry — not a handwritten country table."""

from __future__ import annotations

from functools import lru_cache

import pycountry

# Reserved codes and English exonyms that ISO lookup does not accept as-is.
_EXONYMS: dict[str, str] = {
    "uk": "GB",
    "u.k.": "GB",
    "u.k": "GB",
    "great britain": "GB",
    "britain": "GB",
    "uae": "AE",
    "u.a.e.": "AE",
    "u.a.e": "AE",
    "dubai": "AE",
    "russia": "RU",
    "turkey": "TR",
    "turkiye": "TR",
    "holland": "NL",
    "ivory coast": "CI",
    "palestine": "PS",
}

# Official names whose comma-form would create an ambiguous stem (Korea, Congo, …).
_AMBIGUOUS_STEMS = frozenset(
    {
        "korea",
        "congo",
        "guinea",
        "sudan",
        "samoa",
        "virgin islands",
    }
)


@lru_cache(maxsize=1)
def country_options() -> tuple[dict[str, str], ...]:
    rows = [
        {"id": country.alpha_2, "label": country.name}
        for country in pycountry.countries
        if getattr(country, "alpha_2", None)
    ]
    rows.sort(key=lambda item: item["label"])
    return tuple(rows)


def coerce_country(value: object) -> object:
    """Normalize to ISO 3166-1 alpha-2 (code, alpha-3, English name, or common exonym)."""
    if value is None or not isinstance(value, str):
        return value
    raw = value.strip()
    if not raw:
        return None
    mapped = _EXONYMS.get(raw.casefold())
    if mapped:
        raw = mapped
    try:
        country = pycountry.countries.lookup(raw)
    except LookupError as exc:
        raise ValueError(
            "Enter a valid ISO 3166-1 country code or country name (e.g. PK or Pakistan)."
        ) from exc
    code = getattr(country, "alpha_2", None)
    if not code:
        raise ValueError(
            "Enter a valid ISO 3166-1 country code or country name (e.g. PK or Pakistan)."
        )
    return code


def country_codes_from_value(value: object) -> list[str]:
    """Pull ISO alpha-2 codes from a string, list, or already-normalized code."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            code = coerce_country(value)
        except ValueError:
            return extract_countries_from_text(value)
        return [code] if isinstance(code, str) else []
    if isinstance(value, list):
        codes: list[str] = []
        for item in value:
            for code in country_codes_from_value(item):
                if code not in codes:
                    codes.append(code)
        return codes
    return []


@lru_cache(maxsize=1)
def _country_names() -> tuple[tuple[str, str], ...]:
    """(casefolded name, alpha_2), longest first — no giant regex compile."""
    pairs: dict[str, str] = dict(_EXONYMS)
    pairs.update(
        {
            "usa": "US",
            "united states": "US",
            "united states of america": "US",
            "united kingdom": "GB",
        }
    )
    for country in pycountry.countries:
        code = getattr(country, "alpha_2", None)
        if not code:
            continue
        for attr in ("name", "official_name", "common_name"):
            raw = getattr(country, attr, None)
            if not isinstance(raw, str) or len(raw) < 4:
                continue
            pairs[raw.casefold()] = code
            stem = raw.split(",", 1)[0].strip()
            if len(stem) >= 4 and stem.casefold() not in _AMBIGUOUS_STEMS:
                pairs[stem.casefold()] = code
    return tuple(sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True))


def extract_countries_from_text(text: str) -> list[str]:
    """High-recall ISO alpha-2 codes mentioned in free text (names, not 2-letter codes)."""
    if not text:
        return []
    blob = text.casefold()
    hits: list[tuple[int, str]] = []
    occupied: list[tuple[int, int]] = []
    for name, code in _country_names():
        start = 0
        while True:
            idx = blob.find(name, start)
            if idx < 0:
                break
            end = idx + len(name)
            left_ok = idx == 0 or not blob[idx - 1].isalnum()
            right_ok = end >= len(blob) or not blob[end].isalnum()
            overlap = any(idx < span[1] and end > span[0] for span in occupied)
            if left_ok and right_ok and not overlap:
                hits.append((idx, code))
                occupied.append((idx, end))
                break
            start = idx + 1
    hits.sort(key=lambda item: item[0])
    codes: list[str] = []
    for _, code in hits:
        if code not in codes:
            codes.append(code)
    return codes
