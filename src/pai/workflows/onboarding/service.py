"""Seed a small Person profile. Chat, documents, and later updates enrich the Vault."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings, get_settings
from pai.kernel.errors import AuthError, ValidationFailedError
from pai.workflows.onboarding.catalog import (
    COUNTRY_FIELDS,
    GOAL_TYPE_FOR_PRIMARY,
    PRIMARY_GOAL_TITLES,
    PrimaryGoal,
    field_enum_catalog,
)
from pai.workflows.onboarding.contracts import (
    CONDITIONAL_FIELDS,
    ONBOARDING_PURPOSE,
    OPTIONAL_FIELDS,
    PATH_CHOICES,
    REQUIRED_FIELDS,
    OnboardingSubmit,
)
from pai.domains.goals.models import Goal
from pai.domains.goals.service import enqueue_goal_intelligence_job, upsert_goal_from_anchors
from pai.domains.goals.types import GoalWriteAction
from pai.domains.student.person.models import Education, Person, Skill, WorkExperience
from pai.domains.student.vault.service import VaultService, grow_vault_schema


def _sparse_get(sparse: dict[str, Any], key: str) -> Any:
    entry = sparse.get(key)
    if isinstance(entry, dict) and "value" in entry:
        return entry["value"]
    return entry


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def onboarding_public_status(
    person: Person | None, settings: Settings | None = None
) -> dict[str, Any]:
    resolved = settings or get_settings()
    completed = person is not None and person.onboarding_completed_at is not None
    payload: dict[str, Any] = {
        "onboardingCompleted": completed,
        "onboardingPath": getattr(person, "onboarding_path", None) if person else None,
        "nextPath": resolved.next_path(onboarding_completed=completed),
    }
    if completed and person is not None and person.onboarding_completed_at is not None:
        payload["onboardingCompletedAt"] = person.onboarding_completed_at.isoformat()
    return payload


class OnboardingService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._vault = VaultService(self._settings)

    def _result(self, person: Person) -> dict[str, Any]:
        payload = onboarding_public_status(person, self._settings)
        payload["completed"] = payload["onboardingCompleted"]
        payload["path"] = payload["onboardingPath"]
        if "onboardingCompletedAt" in payload:
            payload["completedAt"] = payload["onboardingCompletedAt"]
        payload["identity"] = {
            "fullName": person.full_name,
            "email": person.email,
            "phone": person.phone,
        }
        return payload

    async def status(self, session: AsyncSession, person: Person) -> dict[str, Any]:
        self._require_vault(person)
        if person.onboarding_completed_at is not None:
            return self._result(person)
        sparse = await self._vault.get_sparse_fields(
            session, person, include_sensitive=True
        )
        education = await self._first_education(session, person)
        goal = await self._first_goal(session, person)
        values = self._current_values(person, sparse, education, goal)
        values["skills"] = await self._skill_values(session, person)
        values["workExperience"] = await self._work_values(session, person)
        missing = self._missing_required(values)
        public = onboarding_public_status(person, self._settings)
        return {
            **public,
            "completed": False,
            "path": person.onboarding_path,
            "choices": PATH_CHOICES if not person.onboarding_path else [],
            "purpose": ONBOARDING_PURPOSE,
            "vaultEnrichment": "chat_and_documents",
            "canComplete": not missing,
            "missingRequired": missing,
            "requiredFields": REQUIRED_FIELDS,
            "conditionalFields": CONDITIONAL_FIELDS,
            "optionalFields": OPTIONAL_FIELDS,
            "countryFields": list(COUNTRY_FIELDS),
            "enums": field_enum_catalog(),
            "identity": {
                "fullName": person.full_name,
                "email": person.email,
                "phone": person.phone,
            },
            "values": values,
        }

    async def submit(
        self, session: AsyncSession, person: Person, body: OnboardingSubmit
    ) -> dict[str, Any]:
        """Map the starting profile into the Vault and mark onboarding complete. Idempotent."""
        self._require_vault(person)
        person.onboarding_path = body.path or person.onboarding_path or "manual"
        await self._apply_submit(session, person, body)
        await self._touch_vault(session, person)
        if person.onboarding_completed_at is None:
            person.onboarding_completed_at = datetime.now(UTC)
        from pai.domains.journey.service import record_onboarding

        title = (body.goalDetail or PRIMARY_GOAL_TITLES[body.primaryGoal.value])[:256]
        await record_onboarding(session, person.id, intent=title)
        await session.commit()
        return self._result(person)

    async def ingest_cv(
        self,
        session: AsyncSession,
        person: Person,
        *,
        filename: str,
        content_type: str,
        data: bytes,
        storage: Any,
        gateway: Any,
    ) -> dict[str, Any]:
        """Extract CV facts into the vault and mark onboarding complete."""
        self._require_vault(person)
        person.onboarding_path = "cv"
        from pai.domains.documents.models import DocumentJob
        from pai.intelligences.documents.ingest import create_document_upload
        from pai.intelligences.documents.workers.analysis_worker import process_document_job

        doc = await create_document_upload(
            session,
            self._settings,
            person,
            filename=filename,
            content_type=content_type,
            data=data,
            storage=storage,
            source_type="onboarding",
            document_type="resume",
            created_by="student",
        )
        result = await session.execute(
            select(DocumentJob)
            .where(DocumentJob.document_id == doc.id)
            .order_by(DocumentJob.created_at.desc())
        )
        job = result.scalars().first()
        if job is None:
            raise AuthError(
                code="CV_EXTRACT_FAILED",
                message="CV upload was saved but no extraction job was created.",
                status_code=502,
            )
        try:
            await process_document_job(
                session, self._settings, job, storage=storage, gateway=gateway
            )
            await session.commit()
        except AuthError:
            raise
        except Exception as exc:
            job.status = "failed"
            job.last_error = str(exc)[:500]
            await session.commit()
            raise AuthError(
                code="CV_EXTRACT_FAILED",
                message="Could not extract your CV. Try a text-based PDF or DOCX.",
                status_code=502,
            ) from exc
        if job.status == "failed":
            raise ValidationFailedError(
                job.last_error
                or (
                    "Could not read text from this file. "
                    "Upload a text-based PDF or DOCX, not a scan."
                )
            )
        if person.onboarding_completed_at is None:
            person.onboarding_completed_at = datetime.now(UTC)
        await self._touch_vault(session, person)
        from pai.domains.journey.service import record_onboarding

        goal = await self._first_goal(session, person)
        await record_onboarding(session, person.id, intent=goal.title if goal else None)
        await session.commit()
        return self._result(person)

    def _require_vault(self, person: Person) -> None:
        if person.vault is None:
            raise AuthError(
                code="VAULT_NOT_READY",
                message="Person vault not initialized. Call POST /api/v1/person/bootstrap.",
                status_code=400,
            )

    async def _touch_vault(self, session: AsyncSession, person: Person) -> None:
        if person.vault:
            grow_vault_schema(person.vault)

    async def _apply_submit(
        self, session: AsyncSession, person: Person, body: OnboardingSubmit
    ) -> None:
        person.phone = body.phone
        consents = ["demographics"]
        if body.budget is not None or body.scholarships is not None:
            consents.append("finance")
        await self._vault.ensure_consents(session, person.id, consents)

        updates: list[tuple[str, Any]] = [
            ("demographics.date_of_birth", body.dateOfBirth.isoformat()),
            ("demographics.nationality", body.nationality),
            ("location.current_country", body.currentCountry),
            ("location.current_city", body.currentCity),
            ("identity.current_status", body.currentStatus.value),
            ("demographics.gender", body.gender.value),
            ("education.highest_level", body.educationLevel.value),
        ]
        if body.linkedinUrl:
            updates.append(("social.linkedin_url", body.linkedinUrl))
        destinations = list(body.targetCountries)
        if body.studyCountry and body.studyCountry not in destinations:
            destinations.insert(0, body.studyCountry)
        if destinations:
            updates.append(("application.study_country", destinations[0]))
            if len(destinations) > 1:
                updates.append(("mobility.preferred_regions", destinations))
        if body.intake:
            cycle = body.intake.value
            if body.intakeYear:
                cycle = f"{cycle} {body.intakeYear}"
            updates.append(("application.admission_cycle", cycle))
        if body.budget:
            updates.append(("finance.funding_status", body.budget.value))
        if body.scholarships is not None:
            updates.append(("finance.scholarship_interest", body.scholarships))
        if body.testScores:
            updates.append(
                (
                    "application.test_scores",
                    [{"name": item.name.value, "score": item.score} for item in body.testScores],
                )
            )
        await self._vault.upsert_sparse_fields(
            session, person, updates, skip_consent_check=True
        )
        await self._upsert_education(session, person, body)
        await self._upsert_goal(session, person, body)
        await self._upsert_skills(session, person, body)
        await self._upsert_work(session, person, body)

    async def _upsert_education(
        self, session: AsyncSession, person: Person, body: OnboardingSubmit
    ) -> None:
        if not (
            body.institution
            or body.degree
            or body.major
            or body.gpa is not None
            or body.graduationYear is not None
        ):
            return
        degree = body.resolved_degree()
        row = (
            None
            if person.onboarding_completed_at is None
            else await self._first_education(session, person)
        )
        if row is None:
            if not body.institution:
                return
            session.add(
                Education(
                    person_id=person.id,
                    institution=body.institution,
                    degree=degree,
                    major=body.major.value if body.major else None,
                    gpa=body.gpa,
                    graduation_year=body.graduationYear,
                    status="completed",
                )
            )
        else:
            if body.institution:
                row.institution = body.institution
            if degree:
                row.degree = degree
            if body.major is not None:
                row.major = body.major.value
            if body.gpa is not None:
                row.gpa = body.gpa
            if body.graduationYear is not None:
                row.graduation_year = body.graduationYear
        if person.vault:
            scopes = list(person.vault.applicable_scopes or [])
            if "education" not in scopes:
                person.vault.applicable_scopes = scopes + ["education"]

    async def _upsert_goal(
        self, session: AsyncSession, person: Person, body: OnboardingSubmit
    ) -> None:
        goal_key = body.primaryGoal.value
        title = (body.goalDetail or PRIMARY_GOAL_TITLES[goal_key])[:256]
        goal_type = GOAL_TYPE_FOR_PRIMARY[goal_key]
        anchors: dict[str, Any] = {"goal_type": goal_type, "title": title}
        if body.studyCountry:
            anchors["target_country"] = body.studyCountry
        goal, action = await upsert_goal_from_anchors(
            session,
            person.id,
            goal_type=goal_type,
            title=title,
            anchors=anchors,
            activate=True,
        )
        if goal is not None:
            goal.description = goal_key
            if action != GoalWriteAction.REINFORCE:
                await enqueue_goal_intelligence_job(session, goal)
        if person.vault:
            scopes = list(person.vault.applicable_scopes or [])
            if "application" not in scopes:
                person.vault.applicable_scopes = scopes + ["application"]

    async def _upsert_skills(
        self, session: AsyncSession, person: Person, body: OnboardingSubmit
    ) -> None:
        if not body.skills:
            return
        known: set[str] = set()
        if person.onboarding_completed_at is not None:
            existing = await session.execute(select(Skill).where(Skill.person_id == person.id))
            known = {row.name.strip().lower() for row in existing.scalars() if row.name}
        for item in body.skills:
            key = item.name.strip().lower()
            if key in known:
                continue
            known.add(key)
            session.add(
                Skill(
                    person_id=person.id,
                    name=item.name.strip(),
                    proficiency=item.proficiency.value if item.proficiency else None,
                )
            )
        if person.vault:
            scopes = list(person.vault.applicable_scopes or [])
            if "career" not in scopes:
                person.vault.applicable_scopes = scopes + ["career"]

    async def _upsert_work(
        self, session: AsyncSession, person: Person, body: OnboardingSubmit
    ) -> None:
        if not body.workExperience:
            return
        known: set[tuple[str, str]] = set()
        if person.onboarding_completed_at is not None:
            existing = await session.execute(
                select(WorkExperience).where(WorkExperience.person_id == person.id)
            )
            known = {
                (row.organization.strip().lower(), row.title.strip().lower())
                for row in existing.scalars()
                if row.organization and row.title
            }
        for item in body.workExperience:
            key = (item.organization.strip().lower(), item.title.strip().lower())
            if key in known:
                continue
            known.add(key)
            session.add(
                WorkExperience(
                    person_id=person.id,
                    organization=item.organization.strip(),
                    title=item.title.strip(),
                    employment_type=item.employmentType.value if item.employmentType else None,
                    is_current=item.isCurrent,
                    description=item.description,
                )
            )
        if person.vault:
            scopes = list(person.vault.applicable_scopes or [])
            if "career" not in scopes:
                person.vault.applicable_scopes = scopes + ["career"]

    def _current_values(
        self,
        person: Person,
        sparse: dict[str, Any],
        education: Education | None,
        goal: Goal | None,
    ) -> dict[str, Any]:
        return {
            "phone": person.phone,
            "dateOfBirth": _sparse_get(sparse, "demographics.date_of_birth"),
            "nationality": _sparse_get(sparse, "demographics.nationality"),
            "currentCountry": _sparse_get(sparse, "location.current_country"),
            "currentCity": _sparse_get(sparse, "location.current_city"),
            "currentStatus": _sparse_get(sparse, "identity.current_status"),
            "gender": _sparse_get(sparse, "demographics.gender"),
            "linkedinUrl": _sparse_get(sparse, "social.linkedin_url"),
            "educationLevel": _sparse_get(sparse, "education.highest_level"),
            "institution": education.institution if education else None,
            "degree": education.degree if education else None,
            "major": education.major if education else None,
            "gpa": education.gpa if education else None,
            "graduationYear": education.graduation_year if education else None,
            "primaryGoal": (
                goal.description
                if goal and goal.description in {item.value for item in PrimaryGoal}
                else None
            ),
            "goalDetail": goal.title if goal else None,
            "studyCountry": _sparse_get(sparse, "application.study_country"),
            "intake": _sparse_get(sparse, "application.admission_cycle"),
            "budget": _sparse_get(sparse, "finance.funding_status"),
            "scholarships": _sparse_get(sparse, "finance.scholarship_interest"),
        }

    def _missing_required(self, values: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for name in REQUIRED_FIELDS:
            if not _present(values.get(name)):
                missing.append(name)
        return missing

    async def _first_education(
        self, session: AsyncSession, person: Person
    ) -> Education | None:
        result = await session.execute(
            select(Education)
            .where(Education.person_id == person.id)
            .order_by(Education.created_at.asc())
        )
        return result.scalars().first()

    async def _first_goal(self, session: AsyncSession, person: Person) -> Goal | None:
        result = await session.execute(
            select(Goal)
            .where(Goal.person_id == person.id)
            .order_by(Goal.created_at.asc())
        )
        return result.scalars().first()

    async def _skill_values(self, session: AsyncSession, person: Person) -> list[dict[str, Any]]:
        result = await session.execute(select(Skill).where(Skill.person_id == person.id))
        return [
            {"name": row.name, "proficiency": row.proficiency}
            for row in result.scalars()
            if row.name
        ]

    async def _work_values(self, session: AsyncSession, person: Person) -> list[dict[str, Any]]:
        result = await session.execute(
            select(WorkExperience).where(WorkExperience.person_id == person.id)
        )
        return [
            {
                "organization": row.organization,
                "title": row.title,
                "employmentType": row.employment_type,
                "isCurrent": row.is_current,
                "description": row.description,
            }
            for row in result.scalars()
        ]
