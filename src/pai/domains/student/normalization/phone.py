"""Phone numbers via Google libphonenumber; stored as E.164."""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def normalize_phone(value: str, *, default_region: str | None = None) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("Enter a valid phone number.")
    region = default_region.strip().upper() if default_region else None
    if region and len(region) != 2:
        region = None
    try:
        parsed = phonenumbers.parse(raw, region)
    except NumberParseException as exc:
        if not raw.startswith("+") and region is None:
            raise ValueError(
                "Enter a valid phone number with country code (e.g. +923001234567)."
            ) from exc
        raise ValueError("Enter a valid phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Enter a valid phone number.")
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
