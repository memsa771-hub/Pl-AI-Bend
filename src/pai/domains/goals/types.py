"""Canonical goal vocabulary. Intelligence classifies; this module validates."""

from __future__ import annotations

from enum import StrEnum


class GoalType(StrEnum):
    ADMISSION = "admission"
    JOB = "job"
    INTERNSHIP = "internship"
    GENERAL = "general"

    @classmethod
    def coerce(cls, value: str | None) -> GoalType:
        raw = (value or "").strip().lower()
        aliases = {
            "admission": cls.ADMISSION,
            "application": cls.ADMISSION,
            "job": cls.JOB,
            "career": cls.JOB,
            "placement": cls.JOB,
            "professional": cls.JOB,
            "internship": cls.INTERNSHIP,
            "intern": cls.INTERNSHIP,
            "general": cls.GENERAL,
            "exploration": cls.GENERAL,
            "exploring": cls.GENERAL,
            "tracking": cls.GENERAL,
            "journey_tracker": cls.GENERAL,
        }
        return aliases.get(raw, cls.GENERAL)


class GoalWriteAction(StrEnum):
    CREATE = "create"
    CREATE_SECONDARY = "create_secondary"
    SWITCH = "switch"
    REINFORCE = "reinforce"
    NONE = "none"
