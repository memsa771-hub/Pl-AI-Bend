from __future__ import annotations

import re
from datetime import date
from typing import Any

_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")


def parse_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if match := _ISO.match(text):
        return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
    if match := _DMY.match(text):
        day, month, year = int(match[1]), int(match[2]), int(match[3])
        if month > 12 and day <= 12:
            day, month = month, day
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    return None
