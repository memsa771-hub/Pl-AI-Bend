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
            return []
        return [code] if isinstance(code, str) else []
    if isinstance(value, list):
        codes: list[str] = []
        for item in value:
            for code in country_codes_from_value(item):
                if code not in codes:
                    codes.append(code)
        return codes
    return []


def extract_countries_from_text(text: str) -> list[str]:
    """Compatibility API: normalize an explicit country value, never interpret prose."""
    return country_codes_from_value(text)
