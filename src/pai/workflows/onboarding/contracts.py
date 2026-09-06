"""Lightweight onboarding seed. Chat, CV, and later updates enrich the Person Vault."""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

from pai.domains.student.normalization.geo import coerce_country
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
from pai.workflows.onboarding.catalog import (
    DEGREE_FOR_LEVEL,
    ENUM_LABELS,
    OnboardingPath,
    PrimaryGoal,
)
from pai.domains.student.normalization.phone import normalize_phone

PATH_CHOICES = [
    {
        "id": OnboardingPath.manual.value,
        "label": ENUM_LABELS["path"][OnboardingPath.manual.value],
        "description": (
            "A short starting profile so PAI can advise from the first chat. "
            "Deeper facts come from conversation."
        ),
    },
    {
        "id": OnboardingPath.cv.value,
        "label": ENUM_LABELS["path"][OnboardingPath.cv.value],
        "description": (
            "Upload your CV. PAI extracts your profile and unlocks chat — no extra form."
        ),
    },
]

ONBOARDING_PURPOSE = (
    "Onboarding is a lightweight starting profile, not the main way to fill the Person Vault. "
    "Chat, CV/document extraction, and later updates continuously enrich the same Vault."
)

REQUIRED_FIELDS = [
    "phone",
    "dateOfBirth",
    "nationality",
    "gender",
    "currentCountry",
    "currentCity",
    "currentStatus",
    "educationLevel",
    "primaryGoal",
]

CONDITIONAL_FIELDS = [
    "institution",
    "degree",
    "major",
    "otherLevelLabel",
]

OPTIONAL_FIELDS = [
    "goalDetail",
    "linkedinUrl",
    "gpa",
    "graduationYear",
    "skills",
    "workExperience",
    "targetCountries",
    "studyCountry",
    "intake",
    "intakeYear",
    "budget",
    "scholarships",
    "testScores",
]


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _reasonable_dob(value: date) -> date:
    today = date.today()
    if value >= today:
        raise ValueError("Date of birth must be in the past.")
    oldest = today - timedelta(days=365 * 100)
    youngest = today - timedelta(days=365 * 13)
    if value < oldest or value > youngest:
        raise ValueError("Date of birth must be for someone between 13 and 100 years old.")
    return value


def _linkedin_url(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    parsed = urlparse(raw if "://" in raw else f"https://{raw}", allow_fragments=True)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "linkedin.com" or host.endswith(".linkedin.com")
    ):
        raise ValueError("LinkedIn URL must be a linkedin.com profile link.")
    return parsed.geturl()


class OnboardingSkillItem(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    proficiency: SkillProficiency | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return _blank_to_none(value) if isinstance(value, str) else value


class OnboardingWorkItem(BaseModel):
    organization: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=256)
    employmentType: EmploymentType | None = None
    isCurrent: bool = False
    description: str | None = Field(default=None, max_length=4000)

    @field_validator("organization", "title", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return _blank_to_none(value) if isinstance(value, str) else value


class OnboardingTestScoreItem(BaseModel):
    name: StandardizedTest
    score: str = Field(min_length=1, max_length=64, examples=["7.5"])

    @field_validator("score", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return _blank_to_none(value) if isinstance(value, str) else value


class OnboardingSubmit(BaseModel):
    """Starting profile. Categorical fields are closed enums; GET /onboarding returns choices."""

    path: OnboardingPath | None = Field(
        default=None,
        description=(
            "manual (form) or cv (confirm after extract). "
            "Defaults to the path already chosen, else manual."
        ),
    )
    phone: str = Field(min_length=8, max_length=32, examples=["+923001234567"])
    dateOfBirth: date = Field(examples=["2004-03-12"])
    nationality: str = Field(min_length=2, max_length=2, examples=["PK"])
    currentCountry: str = Field(min_length=2, max_length=2, examples=["PK"])
    currentCity: str = Field(min_length=2, max_length=128, examples=["Lahore"])
    currentStatus: CurrentStatus
    educationLevel: EducationLevel
    institution: str | None = Field(
        default=None, max_length=256, examples=["University of Toronto"]
    )
    degree: str | None = Field(default=None, max_length=128, examples=["BSCS"])
    major: FieldOfStudy | None = None
    otherLevelLabel: str | None = Field(
        default=None,
        max_length=128,
        examples=["A-Levels"],
        description=(
            "Only when educationLevel is `other`: the name of that qualification "
            "(e.g. A-Levels, IB, CA). Ignored for high_school / diploma / bachelor / master / phd."
        ),
    )
    primaryGoal: PrimaryGoal
    goalDetail: str | None = Field(
        default=None,
        max_length=256,
        examples=["MS Computer Science in Germany"],
        description="Optional note when primaryGoal is admission/placement/etc.",
    )
    gender: Gender
    linkedinUrl: str | None = Field(default=None, max_length=512)
    gpa: float | None = Field(default=None, ge=0, le=4)
    graduationYear: int | None = Field(default=None, ge=1950, le=2100)
    skills: list[OnboardingSkillItem] = Field(default_factory=list)
    workExperience: list[OnboardingWorkItem] = Field(default_factory=list)
    targetCountries: list[str] = Field(default_factory=list)
    studyCountry: str | None = Field(default=None, min_length=2, max_length=2, examples=["DE"])
    intake: IntakeSeason | None = None
    intakeYear: int | None = Field(default=None, ge=2020, le=2100)
    budget: BudgetBand | None = None
    scholarships: bool | None = None
    testScores: list[OnboardingTestScoreItem] = Field(default_factory=list)

    @field_validator("nationality", "currentCountry", "studyCountry", mode="before")
    @classmethod
    def country_code(cls, value: object) -> object:
        return coerce_country(value)

    @field_validator("currentCity", mode="before")
    @classmethod
    def strip_city(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "institution",
        "degree",
        "otherLevelLabel",
        "linkedinUrl",
        "goalDetail",
        mode="before",
    )
    @classmethod
    def empty_optional(cls, value: object) -> object:
        return _blank_to_none(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def phone_e164(self):
        self.phone = normalize_phone(self.phone, default_region=self.currentCountry)
        return self

    @field_validator("dateOfBirth")
    @classmethod
    def dob(cls, value: date) -> date:
        return _reasonable_dob(value)

    @field_validator("linkedinUrl")
    @classmethod
    def linkedin(cls, value: str | None) -> str | None:
        return _linkedin_url(value)

    @field_validator("skills", mode="before")
    @classmethod
    def coerce_skills(cls, value: object) -> object:
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        out: list[object] = []
        for item in value:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    out.append({"name": name})
            else:
                out.append(item)
        return out

    @field_validator("targetCountries", mode="before")
    @classmethod
    def coerce_countries(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            code = coerce_country(value)
            return [code] if code else []
        if isinstance(value, list):
            out: list[object] = []
            for item in value:
                code = coerce_country(item)
                if code:
                    out.append(code)
            return out
        return value

    def resolved_degree(self) -> str | None:
        if self.degree:
            return self.degree
        if self.educationLevel == EducationLevel.other and self.otherLevelLabel:
            return self.otherLevelLabel
        if self.major:
            return None
        return DEGREE_FOR_LEVEL.get(self.educationLevel)
