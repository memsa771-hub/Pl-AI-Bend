from __future__ import annotations

import pytest
from conftest import ONBOARDING_PAYLOAD
from pydantic import ValidationError

from pai.workflows.onboarding.catalog import field_enum_catalog
from pai.workflows.onboarding.contracts import PATH_CHOICES, OnboardingSubmit


def test_enum_catalog_exposes_dropdown_ids():
    catalog = field_enum_catalog()
    assert {item["id"] for item in catalog["primaryGoal"]} == {
        "exploring",
        "placement",
        "admission",
        "professional",
        "journey_tracker",
    }
    assert any(item["id"] == "PK" for item in catalog["countries"])
    assert len(catalog["countries"]) > 200
    assert "currentCountry" not in catalog
    assert "phd" in {item["id"] for item in catalog["educationLevel"]}
    assert "fully_funded" in {item["id"] for item in catalog["budget"]}



def test_submit_schema_requires_critical_fields():
    with pytest.raises(ValidationError) as exc:
        OnboardingSubmit.model_validate({"nationality": "Pakistani"})
    names = {error["loc"][-1] for error in exc.value.errors()}
    for field in (
        "phone",
        "dateOfBirth",
        "currentCountry",
        "currentCity",
        "currentStatus",
        "educationLevel",
        "gender",
        "primaryGoal",
    ):
        assert field in names
    assert "institution" not in names
    assert "degree" not in names


def test_submit_schema_optional_fields_can_be_omitted():
    body = OnboardingSubmit.model_validate(ONBOARDING_PAYLOAD)
    assert body.linkedinUrl is None
    assert body.skills == []
    assert body.workExperience == []
    assert body.testScores == []


def test_submit_schema_minimal_criticals_are_enough():
    body = OnboardingSubmit.model_validate(
        {
            "phone": "+923001234567",
            "dateOfBirth": "2004-03-12",
            "nationality": "PK",
            "currentCountry": "PK",
            "currentCity": "Lahore",
            "currentStatus": "student",
            "gender": "male",
            "educationLevel": "bachelor",
            "primaryGoal": "admission",
        }
    )
    assert body.institution is None
    assert body.degree is None
    assert body.gpa is None


def test_submit_schema_high_school_does_not_need_degree():
    payload = {
        **ONBOARDING_PAYLOAD,
        "educationLevel": "high_school",
        "institution": "City School",
        "degree": None,
        "major": None,
    }
    body = OnboardingSubmit.model_validate(payload)
    assert body.resolved_degree() == "High School"


def test_submit_schema_rejects_vague_primary_goal():
    payload = {**ONBOARDING_PAYLOAD, "primaryGoal": "MS Computer Science in Germany"}
    with pytest.raises(ValidationError):
        OnboardingSubmit.model_validate(payload)


def test_submit_schema_accepts_country_name_alias():
    payload = {**ONBOARDING_PAYLOAD, "nationality": "Pakistan", "currentCountry": "Germany"}
    body = OnboardingSubmit.model_validate(payload)
    assert body.nationality == "PK"
    assert body.currentCountry == "DE"
    uk = OnboardingSubmit.model_validate({**ONBOARDING_PAYLOAD, "currentCountry": "UK"})
    assert uk.currentCountry == "GB"
    alpha3 = OnboardingSubmit.model_validate({**ONBOARDING_PAYLOAD, "currentCountry": "DEU"})
    assert alpha3.currentCountry == "DE"
    turkey = OnboardingSubmit.model_validate({**ONBOARDING_PAYLOAD, "currentCountry": "Turkey"})
    assert turkey.currentCountry == "TR"


def test_submit_schema_rejects_unknown_country():
    with pytest.raises(ValidationError, match="ISO 3166-1"):
        OnboardingSubmit.model_validate({**ONBOARDING_PAYLOAD, "currentCountry": "Narnia"})


def test_submit_schema_normalizes_phone_to_e164():
    body = OnboardingSubmit.model_validate(
        {**ONBOARDING_PAYLOAD, "phone": "0300 1234567", "currentCountry": "PK"}
    )
    assert body.phone == "+923001234567"
    with pytest.raises(ValidationError, match="phone"):
        OnboardingSubmit.model_validate({**ONBOARDING_PAYLOAD, "phone": "12345"})




def test_onboarding_required_before_chat(verified_user):
    client, headers, _ = verified_user
    blocked = client.post("/api/v1/chat", headers=headers, json={"message": "Hello"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ONBOARDING_INCOMPLETE"


def test_login_does_not_complete_onboarding(verified_user):
    client, headers, _ = verified_user
    status = client.get("/api/v1/onboarding", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()["data"]
    assert body["completed"] is False
    assert body["onboardingCompleted"] is False
    assert body["nextPath"] == "/onboarding"
    me = client.get("/api/v1/person/me", headers=headers).json()["data"]
    assert me["onboardingCompleted"] is False


def test_onboarding_offers_manual_or_cv_choice(verified_user):
    client, headers, _ = verified_user
    status = client.get("/api/v1/onboarding", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()["data"]
    assert body["completed"] is False
    assert body["path"] is None
    ids = {c["id"] for c in body["choices"]}
    assert ids == {"manual", "cv"}
    assert "phone" in body["requiredFields"]
    assert "gender" in body["requiredFields"]
    assert "primaryGoal" in body["requiredFields"]
    assert "institution" not in body["requiredFields"]
    assert "institution" in body["conditionalFields"]
    assert "linkedinUrl" in body["optionalFields"]
    goal_ids = {item["id"] for item in body["enums"]["primaryGoal"]}
    assert goal_ids == {
        "exploring",
        "placement",
        "admission",
        "professional",
        "journey_tracker",
    }
    assert any(item["id"] == "PK" for item in body["enums"]["countries"])
    assert "currentCountry" not in body["enums"]
    assert body["countryFields"] == [
        "nationality",
        "currentCountry",
        "studyCountry",
        "targetCountries",
    ]
    assert {item["id"] for item in body["enums"]["educationLevel"]} >= {
        "high_school",
        "diploma",
        "bachelor",
        "master",
        "phd",
        "other",
    }
    assert "nationalId" not in body["requiredFields"]
    assert body["vaultEnrichment"] == "chat_and_documents"
    assert "starting profile" in body["purpose"].lower() or "lightweight" in body["purpose"].lower()


def test_incomplete_submit_is_rejected(verified_user):
    client, headers, _ = verified_user
    incomplete = client.post(
        "/api/v1/onboarding",
        headers=headers,
        json={"path": "manual", "nationality": "Pakistani"},
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "VALIDATION_ERROR"
    status = client.get("/api/v1/onboarding", headers=headers).json()["data"]
    assert status["completed"] is False
    assert status["onboardingCompleted"] is False


def test_manual_onboarding_unlocks_pai(verified_user):
    client, headers, _ = verified_user
    done = client.post("/api/v1/onboarding", headers=headers, json=ONBOARDING_PAYLOAD)
    assert done.status_code == 200, done.text
    data = done.json()["data"]
    assert data["onboardingCompleted"] is True
    assert data["nextPath"] != "/onboarding"
    assert data["onboardingPath"] == "manual"
    assert "enums" not in data
    assert "values" not in data
    assert "requiredFields" not in data
    after = client.get("/api/v1/onboarding", headers=headers).json()["data"]
    assert after["onboardingCompleted"] is True
    assert "enums" not in after

    me = client.get("/api/v1/person/me", headers=headers).json()["data"]
    assert me["onboardingCompleted"] is True
    assert me["phone"] == "+923001234567"
    educations = client.get("/api/v1/person/educations", headers=headers).json()["data"]["items"]
    assert educations[0]["institution"] == "Bahria University"
    goals = client.get("/api/v1/person/goals", headers=headers).json()["data"]["items"]
    assert goals[0]["title"] == "MS Computer Science in Germany"


def test_onboarding_submit_is_idempotent(verified_user):
    client, headers, _ = verified_user
    first = client.post("/api/v1/onboarding", headers=headers, json=ONBOARDING_PAYLOAD)
    assert first.status_code == 200, first.text
    completed_at = first.json()["data"]["onboardingCompletedAt"]
    again = client.post("/api/v1/onboarding", headers=headers, json=ONBOARDING_PAYLOAD)
    assert again.status_code == 200, again.text
    data = again.json()["data"]
    assert data["onboardingCompleted"] is True
    assert data["onboardingCompletedAt"] == completed_at
    educations = client.get("/api/v1/person/educations", headers=headers).json()["data"]["items"]
    assert len(educations) == 1


def test_cv_choice_does_not_require_form_fields():
    cv = next(item for item in PATH_CHOICES if item["id"] == "cv")
    assert "no extra form" in cv["description"].lower()


def test_cv_upload_completes_onboarding_without_form(verified_user, monkeypatch):
    import uuid

    from pai.domains.documents.models import Document, DocumentJob

    client, headers, _ = verified_user

    async def fake_upload(session, settings, person, *, filename, content_type, data, storage, **kwargs):
        del settings, storage
        doc = Document(
            id=uuid.uuid4(),
            person_id=person.id,
            storage_path=f"{person.id}/cv.txt",
            original_filename=filename,
            mime_type=content_type,
            size_bytes=len(data),
            status="uploaded",
            document_type="resume",
        )
        session.add(doc)
        session.add(
            DocumentJob(
                document_id=doc.id,
                person_id=person.id,
                idempotency_key=f"extract-{doc.id}",
                status="pending",
            )
        )
        await session.commit()
        await session.refresh(doc)
        return doc

    async def fake_process(session, settings, job, *, storage, gateway):
        del session, settings, storage, gateway
        job.status = "completed"

    monkeypatch.setattr("pai.intelligences.documents.ingest.create_document_upload", fake_upload)
    monkeypatch.setattr(
        "pai.intelligences.documents.workers.analysis_worker.process_document_job",
        fake_process,
    )

    res = client.post(
        "/api/v1/onboarding/cv",
        headers=headers,
        files={"file": ("cv.txt", b"Aisha Khan intern Python SQL NYU Abu Dhabi", "text/plain")},
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["onboardingCompleted"] is True
    assert data["onboardingPath"] == "cv"
    assert "enums" not in data
    assert "requiredFields" not in data

    blocked = client.post("/api/v1/chat", headers=headers, json={"message": "Hello"})
    assert blocked.json().get("error", {}).get("code") != "ONBOARDING_INCOMPLETE"


def test_form_submit_with_cv_path_tag_still_allowed(verified_user):
    client, headers, _ = verified_user
    payload = {**ONBOARDING_PAYLOAD, "path": "cv"}
    status = client.get("/api/v1/onboarding", headers=headers).json()["data"]
    assert status["completed"] is False
    assert "phone" in status["missingRequired"]
    assert "dateOfBirth" in status["missingRequired"]
    assert "primaryGoal" in status["missingRequired"]

    done = client.post("/api/v1/onboarding", headers=headers, json=payload)
    assert done.status_code == 200, done.text
    data = done.json()["data"]
    assert data["onboardingCompleted"] is True
    assert data["onboardingPath"] == "cv"
    assert "enums" not in data


def test_vault_batch_write_uses_one_select():
    import asyncio
    import uuid
    from types import SimpleNamespace

    from cryptography.fernet import Fernet

    from pai.domains.student.person.models import VaultEvidence, VaultHistory, VaultValue
    from pai.domains.student.vault.service import VaultService

    class _Session:
        def __init__(self) -> None:
            self.queries = 0
            self.flushes = 0
            self.added: list[object] = []

        async def execute(self, _stmt):
            self.queries += 1
            return SimpleNamespace(scalars=lambda: [])

        async def flush(self) -> None:
            self.flushes += 1

        def add(self, obj) -> None:
            self.added.append(obj)

    async def _run() -> None:
        svc = VaultService(
            SimpleNamespace(vault_encryption_key=Fernet.generate_key().decode())
        )
        person = SimpleNamespace(
            id=uuid.uuid4(),
            vault=SimpleNamespace(id=uuid.uuid4(), applicable_scopes=["universal"]),
        )
        session = _Session()
        await svc.upsert_sparse_fields(
            session,
            person,
            [
                ("demographics.nationality", "PK"),
                ("location.current_city", "Lahore"),
                ("demographics.gender", "male"),
            ],
            skip_consent_check=True,
        )
        assert session.queries == 1
        assert session.flushes == 1
        kinds = [type(obj) for obj in session.added]
        assert kinds.count(VaultValue) == 3
        assert kinds.count(VaultEvidence) == 3
        assert kinds.count(VaultHistory) == 3

    asyncio.run(_run())
