"""OpenTelemetry hooks. No-op until OTEL is configured — never logs PII values."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger("pai.document_intelligence")


@contextmanager
def span(name: str, **attrs: object) -> Iterator[None]:
    safe = {key: value for key, value in attrs.items() if "value" not in key.lower()}
    logger.debug("span.start %s %s", name, safe)
    try:
        yield
    except Exception:
        logger.debug("span.error %s", name)
        raise
    else:
        logger.debug("span.end %s", name)
