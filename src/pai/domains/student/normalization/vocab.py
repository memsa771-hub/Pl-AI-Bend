"""Shared closed student value types. Form labels live in the onboarding workflow."""

from __future__ import annotations

from enum import StrEnum


class Gender(StrEnum):
    male = "male"
    female = "female"
    non_binary = "non_binary"
    prefer_not_to_say = "prefer_not_to_say"
    other = "other"


class CurrentStatus(StrEnum):
    student = "student"
    graduate = "graduate"
    professional = "professional"
    job_seeker = "job_seeker"
    other = "other"


class EducationLevel(StrEnum):
    high_school = "high_school"
    diploma = "diploma"
    bachelor = "bachelor"
    master = "master"
    phd = "phd"
    other = "other"


class FieldOfStudy(StrEnum):
    computer_science = "computer_science"
    software_engineering = "software_engineering"
    data_science = "data_science"
    artificial_intelligence = "artificial_intelligence"
    engineering = "engineering"
    business = "business"
    medicine = "medicine"
    law = "law"
    arts_humanities = "arts_humanities"
    social_sciences = "social_sciences"
    natural_sciences = "natural_sciences"
    other = "other"


class IntakeSeason(StrEnum):
    fall = "fall"
    spring = "spring"
    summer = "summer"
    winter = "winter"
    rolling = "rolling"


class BudgetBand(StrEnum):
    limited = "limited"
    moderate = "moderate"
    comfortable = "comfortable"
    fully_funded = "fully_funded"


class SkillProficiency(StrEnum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"
    expert = "expert"


class EmploymentType(StrEnum):
    internship = "internship"
    part_time = "part_time"
    full_time = "full_time"
    contract = "contract"
    freelance = "freelance"
    other = "other"


class StandardizedTest(StrEnum):
    ielts = "ielts"
    toefl = "toefl"
    pte = "pte"
    duolingo = "duolingo"
    gre = "gre"
    gmat = "gmat"
    sat = "sat"
    act = "act"
    net = "net"
    ecat = "ecat"
    mdcat = "mdcat"
    other = "other"
