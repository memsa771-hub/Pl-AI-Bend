"""Parse only dates whose order and precision are explicit or unambiguous."""
from datetime import date
import re
from typing import Any

def parse_date(value: Any) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
        match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
        if match:
            a, b, year = map(int, match.groups())
            if a > 12 >= b:
                return date(year, b, a).isoformat()
            if b > 12 >= a:
                return date(year, a, b).isoformat()
            if a == b:
                return date(year, a, b).isoformat()
    except ValueError:
        pass
    return None
