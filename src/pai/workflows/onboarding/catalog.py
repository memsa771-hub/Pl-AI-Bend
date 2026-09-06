"""Onboarding form catalog: path/goal choices and dropdown labels."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pai.domains.goals.types import GoalType
from pai.domains.student.normalization.geo import country_options
from pai.domains.student.normalization.vocab import (
    BudgetBand,
    CurrentStatus,
    EducationLevel,
    EmploymentType,
    FieldOfStudy,
    Gender,
    IntakeSeason,
    SkillProficiency,
    StandardizedTest,
)


class OnboardingPath(StrEnum):
    manual = "manual"
    cv = "cv"


class PrimaryGoal(StrEnum):
    exploring = "exploring"
    placement = "placement"
    admission = "admission"
    professional = "professional"
    journey_tracker = "journey_tracker"


ENUM_LABELS: dict[str, dict[str, str]] = {
    "path": {
        "manual": "Complete Onboarding",
        "cv": "Upload My CV",
    },
    "gender": {
        "male": "Male",
        "female": "Female",
        "non_binary": "Non-binary",
        "prefer_not_to_say": "Prefer not to say",
        "other": "Other",
    },
    "currentStatus": {
        "student": "Student",
        "graduate": "Graduate",
        "professional": "Working professional",
        "job_seeker": "Job seeker",
        "other": "Other",
    },
    "educationLevel": {
        "high_school": "High school",
        "diploma": "Diploma / intermediate",
        "bachelor": "Bachelor's",
        "master": "Master's",
        "phd": "PhD / doctorate",
        "other": "Other",
    },
    "primaryGoal": {
        "exploring": "Exploring options",
        "placement": "Placement (jobs / internships)",
        "admission": "University admission",
        "professional": "Professional growth",
        "journey_tracker": "Journey tracker",
    },
    "major": {
        "computer_science": "Computer science",
        "software_engineering": "Software engineering",
        "data_science": "Data science",
        "artificial_intelligence": "Artificial intelligence",
        "engineering": "Engineering",
        "business": "Business",
        "medicine": "Medicine",
        "law": "Law",
        "arts_humanities": "Arts & humanities",
        "social_sciences": "Social sciences",
        "natural_sciences": "Natural sciences",
        "other": "Other",
    },
    "intake": {
        "fall": "Fall",
        "spring": "Spring",
        "summer": "Summer",
        "winter": "Winter",
        "rolling": "Rolling",
    },
    "budget": {
        "limited": "Limited",
        "moderate": "Moderate",
        "comfortable": "Comfortable",
        "fully_funded": "Fully funded / seeking full funding",
    },
    "proficiency": {
        "beginner": "Beginner",
        "intermediate": "Intermediate",
        "advanced": "Advanced",
        "expert": "Expert",
    },
    "employmentType": {
        "internship": "Internship",
        "part_time": "Part-time",
        "full_time": "Full-time",
        "contract": "Contract",
        "freelance": "Freelance",
        "other": "Other",
    },
    "testName": {
        "ielts": "IELTS",
        "toefl": "TOEFL",
        "pte": "PTE",
        "duolingo": "Duolingo",
        "gre": "GRE",
        "gmat": "GMAT",
        "sat": "SAT",
        "act": "ACT",
        "net": "NET",
        "ecat": "ECAT",
        "mdcat": "MDCAT",
        "other": "Other",
    },
}

PRIMARY_GOAL_TITLES = ENUM_LABELS["primaryGoal"]

GOAL_TYPE_FOR_PRIMARY: dict[str, str] = {
    "exploring": GoalType.GENERAL.value,
    "placement": GoalType.JOB.value,
    "admission": GoalType.ADMISSION.value,
    "professional": GoalType.JOB.value,
    "journey_tracker": GoalType.GENERAL.value,
}

DEGREE_FOR_LEVEL: dict[str, str] = {
    "high_school": "High School",
    "diploma": "Diploma",
    "bachelor": "Bachelor's",
    "master": "Master's",
    "phd": "PhD",
    "other": "Other",
}


def _options(values: list[str], labels: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"id": value, "label": labels.get(value, value.replace("_", " ").title())}
        for value in values
    ]


# nationality, currentCountry, studyCountry, targetCountries all bind to enums.countries.
COUNTRY_FIELDS = (
    "nationality",
    "currentCountry",
    "studyCountry",
    "targetCountries",
)


@lru_cache(maxsize=1)
def field_enum_catalog() -> dict[str, list[dict[str, str]]]:
    return {
        "path": _options([m.value for m in OnboardingPath], ENUM_LABELS["path"]),
        "gender": _options([m.value for m in Gender], ENUM_LABELS["gender"]),
        "currentStatus": _options(
            [m.value for m in CurrentStatus], ENUM_LABELS["currentStatus"]
        ),
        "educationLevel": _options(
            [m.value for m in EducationLevel], ENUM_LABELS["educationLevel"]
        ),
        "primaryGoal": _options([m.value for m in PrimaryGoal], ENUM_LABELS["primaryGoal"]),
        "major": _options([m.value for m in FieldOfStudy], ENUM_LABELS["major"]),
        "intake": _options([m.value for m in IntakeSeason], ENUM_LABELS["intake"]),
        "budget": _options([m.value for m in BudgetBand], ENUM_LABELS["budget"]),
        "proficiency": _options(
            [m.value for m in SkillProficiency], ENUM_LABELS["proficiency"]
        ),
        "employmentType": _options(
            [m.value for m in EmploymentType], ENUM_LABELS["employmentType"]
        ),
        "testName": _options([m.value for m in StandardizedTest], ENUM_LABELS["testName"]),
        "countries": list(country_options()),
    }
