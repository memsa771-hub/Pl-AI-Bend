from __future__ import annotations

import re

_SPACE = re.compile(r"\s+")


def normalize_institution(value: str | None) -> str | None:
    if not value:
        return None
    return _SPACE.sub(" ", value).strip()
