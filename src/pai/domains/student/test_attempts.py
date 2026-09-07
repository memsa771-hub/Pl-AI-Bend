"""Standardized tests as repeatable entities.

A flat `test_scores` field loses the thing that matters most about tests: they
are retaken, and old scores expire. Storing attempts separately keeps the
history and makes validity checkable, so "IELTS 6.5" from 2024 is not silently
treated as a current score (doc §20, §34).
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.domains.student.normalization.vocab import StandardizedTest
from pai.domains.student.person.models import TestAttempt

ENTITY_TYPE = "test_attempt"

# How long a score stays accepted by institutions, in years. Tests absent here
# (national entrance exams, "other") have no general expiry rule, so PAI does
# not invent one.
VALIDITY_YEARS: dict[str, int] = {
    "ielts": 2,
    "toefl": 2,
    "pte": 2,
    "duolingo": 2,
    "gre": 5,
    "gmat": 5,
    "sat": 5,
    "act": 5,
}

_KNOWN_TESTS = {member.value for member in StandardizedTest}

# Spellings that map onto a canonical test id.
_TEST_ALIASES: dict[str, str] = {
    "ielts academic": "ielts",
    "ielts general": "ielts",
    "toefl ibt": "toefl",
    "pte academic": "pte",
    "duolingo english test": "duolingo",
    "det": "duolingo",
    "gre general": "gre",
    "sat reasoning": "sat",
    "nts": "net",
    "nat": "net",
}

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_SECTION_KEYS = (
    "listening", "reading", "writing", "speaking",
    "quantitative", "verbal", "analytical", "math", "english",
)


def normalize_test_type(name: Any) -> str | None:
    """Map a test name to a canonical id, or None when unrecognizable."""
    if not name:
        return None
    text = re.sub(r"[^a-z0-9 ]+", " ", str(name).casefold()).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return None
    if text in _TEST_ALIASES:
        return _TEST_ALIASES[text]
    if text in _KNOWN_TESTS:
        return text
    for known in sorted(_KNOWN_TESTS, key=len, reverse=True):
        if known != "other" and re.search(rf"\b{re.escape(known)}\b", text):
            return known
    return StandardizedTest.OTHER.value


def parse_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_RE.search(str(value))
    return float(match.group()) if match else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return date(int(text), 1, 1)
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return date(int(text[:4]), int(text[5:7]), 1)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _sections(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        name = str(key).strip().casefold()
        score = parse_score(value)
        if name and score is not None:
            out[name] = score
    return out


def validity_until(test_type: str, test_date: date | None) -> date | None:
    years = VALIDITY_YEARS.get(test_type)
    if years is None or test_date is None:
        return None
    try:
        return test_date.replace(year=test_date.year + years)
    except ValueError:  # 29 Feb
        return test_date.replace(year=test_date.year + years, day=28)


def attempt_status(expires_on: date | None, *, today: date | None = None) -> str:
    if expires_on is None:
        return "valid"
    return "expired" if expires_on < (today or date.today()) else "valid"


def parse_test_observation(raw: Any) -> dict[str, Any] | None:
    """Normalize one reported test result into a persistable shape."""
    if isinstance(raw, str):
        # e.g. "IELTS 7.5"
        test_type = normalize_test_type(raw)
        score = parse_score(raw)
        if test_type is None or score is None:
            return None
        return {
            "test_type": test_type,
            "original_name": raw.strip()[:128],
            "overall_score": str(score),
            "overall_numeric": score,
            "test_date": None,
            "sections": {},
        }

    if not isinstance(raw, dict):
        return None

    name = raw.get("name") or raw.get("test") or raw.get("type") or raw.get("test_type")
    test_type = normalize_test_type(name)
    if test_type is None:
        return None

    overall_raw = (
        raw.get("score")
        if raw.get("score") is not None
        else raw.get("overall_score", raw.get("overall"))
    )
    numeric = parse_score(overall_raw)
    sections = _sections(raw.get("sections"))
    if not sections:
        sections = {key: parse_score(raw[key]) for key in _SECTION_KEYS if raw.get(key) is not None}
        sections = {k: v for k, v in sections.items() if v is not None}
    if numeric is None and not sections:
        return None

    return {
        "test_type": test_type,
        "original_name": str(name)[:128] if name else None,
        "overall_score": (str(overall_raw)[:32] if overall_raw is not None else None),
        "overall_numeric": numeric,
        "test_date": _parse_date(
            raw.get("test_date") or raw.get("date") or raw.get("year") or raw.get("taken_on")
        ),
        "sections": sections,
    }


def _same_sitting(row: TestAttempt, observation: dict[str, Any]) -> bool:
    """Whether an observation describes an attempt already on record.

    Same test on the same date is the same sitting. With no date, an identical
    overall score is treated as a restatement rather than a new attempt —
    inventing a second sitting from a repeated remark would be worse than
    merging two genuinely different ones the student can correct.
    """
    obs_date = observation.get("test_date")
    if row.test_date and obs_date:
        return row.test_date == obs_date
    numeric = observation.get("overall_numeric")
    if numeric is not None and row.overall_numeric is not None:
        return abs(row.overall_numeric - numeric) < 1e-6
    return row.test_date is None and obs_date is None


async def upsert_test_attempt(
    session: AsyncSession,
    person_id: uuid.UUID,
    observation: dict[str, Any],
    *,
    today: date | None = None,
) -> tuple[TestAttempt, str]:
    """Record one test result, never overwriting a different sitting."""
    test_type = observation["test_type"]
    rows = list(
        (
            await session.execute(
                select(TestAttempt)
                .where(
                    TestAttempt.person_id == person_id,
                    TestAttempt.test_type == test_type,
                )
                .order_by(TestAttempt.attempt_number)
            )
        ).scalars()
    )

    match = next((row for row in rows if _same_sitting(row, observation)), None)
    if match is not None:
        status = "reinforced"
        for attr in ("overall_score", "overall_numeric", "original_name"):
            value = observation.get(attr)
            if value is not None and getattr(match, attr) != value:
                setattr(match, attr, value)
                status = "updated"
        if observation.get("test_date") and match.test_date != observation["test_date"]:
            match.test_date = observation["test_date"]
            status = "updated"
        merged = {**(match.sections or {}), **observation.get("sections", {})}
        if merged != (match.sections or {}):
            match.sections = merged
            status = "updated"
        row = match
    else:
        row = TestAttempt(
            person_id=person_id,
            test_type=test_type,
            original_name=observation.get("original_name"),
            attempt_number=(rows[-1].attempt_number + 1) if rows else 1,
            test_date=observation.get("test_date"),
            overall_score=observation.get("overall_score"),
            overall_numeric=observation.get("overall_numeric"),
            sections=observation.get("sections") or {},
        )
        session.add(row)
        rows.append(row)
        status = "accepted"

    row.validity_until = validity_until(test_type, row.test_date)
    row.status = attempt_status(row.validity_until, today=today)
    await session.flush()
    return row, status


def attempt_dict(row: TestAttempt) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "testType": row.test_type,
        "originalName": row.original_name,
        "attemptNumber": row.attempt_number,
        "testDate": row.test_date.isoformat() if row.test_date else None,
        "overallScore": row.overall_score,
        "overallNumeric": row.overall_numeric,
        "sections": dict(row.sections or {}),
        "validityUntil": row.validity_until.isoformat() if row.validity_until else None,
        "status": row.status,
    }


async def list_test_attempts(
    session: AsyncSession, person_id: uuid.UUID, *, limit: int = 50
) -> list[TestAttempt]:
    return list(
        (
            await session.execute(
                select(TestAttempt)
                .where(TestAttempt.person_id == person_id)
                .order_by(TestAttempt.test_type, TestAttempt.attempt_number)
                .limit(limit)
            )
        ).scalars()
    )
