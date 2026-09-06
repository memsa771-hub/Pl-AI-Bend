"""Staged analysis. OCR/LLM produce evidence; reconciliation decides Vault impact."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from pai.config import Settings
from pai.kernel.gates import accept_vault_candidates
from pai.platform.llm.gateway import LLMGateway
from pai.kernel.contracts.schemas import VaultCandidate
from pai.intelligences.documents.classification.classifier import classify_document
from pai.intelligences.documents.classification.taxonomy import default_type, evidence_eligible
from pai.intelligences.documents.config import policy, taxonomy as di_taxonomy
from pai.intelligences.documents.digitization.service import digitize_bytes
from pai.intelligences.documents.digitization.schemas import DigitizationResult
from pai.intelligences.documents.evidence.authority import source_authority
from pai.intelligences.documents.evidence.grounding import (
    evidence_grounded,
    extraction_confidence,
    page_for_span,
)
from pai.intelligences.documents.evidence.criticality import field_criticality, field_sensitivity
from pai.intelligences.documents.extraction.extractor import extract_candidates
from pai.intelligences.documents.identity.matcher import match_student
from pai.intelligences.documents.identity.parties import expected_roles
from pai.intelligences.documents.normalization import normalize_field
from pai.intelligences.documents.reconciliation.engine import ReconcileInput, reconcile
from pai.intelligences.documents.telemetry.tracing import span
from pai.intelligences.documents.verification.service import open_case
from pai.domains.documents.models import (
    Document,
    DocumentAnalysisRun,
    DocumentCandidate,
    DocumentFact,
    DocumentJob,
    DocumentParty,
    DocumentVersion,
)
from pai.domains.student.person.models import Education, Person, PersonVault, VaultValue
from pai.domains.student.vault.catalog import get_catalog_field
from pai.platform.storage.supabase import SupabaseStorageProvider


def _mark_stage(
    run: DocumentAnalysisRun, job: DocumentJob, stage: str, doc: Document | None = None
) -> None:
    done = list(run.completed_stages or [])
    if run.current_stage and run.current_stage not in done:
        done.append(run.current_stage)
    run.completed_stages = done
    run.current_stage = stage
    job.current_stage = stage
    mapped = (policy().get("processing_stages") or {}).get(stage)
    if doc is not None and mapped:
        doc.status = mapped


async def _known_facts(session: AsyncSession, person: Person) -> list[str]:
    from pai.intelligences.counselor.context import build_known_facts
    from pai.domains.student.person.profile_snapshot import load_typed_profile_records
    from pai.domains.student.vault.service import VaultService

    sparse: dict = {}
    if person.vault is not None:
        unified = await VaultService().get_unified_vault(session, person, include_sensitive=False)
        sparse = unified.get("sparseFields") or {}
    typed = await load_typed_profile_records(session, person.id)
    return build_known_facts(
        identity={"preferredName": person.preferred_name, "fullName": person.full_name},
        sparse=sparse,
        typed=typed,
    )


async def _existing_belief(session: AsyncSession, person: Person, field_key: str):
    field = get_catalog_field(field_key)
    if field is not None and field.storage == "person" and field.person_column:
        return getattr(person, field.person_column, None) or person.preferred_name
    spec = (policy().get("typed_belief") or {}).get(field_key) or {}
    if spec.get("storage") == "educations":
        row = await session.scalar(
            select(Education)
            .where(Education.person_id == person.id)
            .order_by(Education.updated_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        raw = getattr(row, str(spec.get("column") or "gpa"), None)
        if raw is None:
            return None
        scale_col = spec.get("scale_column")
        scale = getattr(row, str(scale_col), None) if scale_col else 4.0
        if spec.get("shape") == "cumulative_gpa":
            return {"value": float(raw), "scale": float(scale or 4.0), "type": "cumulative"}
        return raw
    vault_id = await session.scalar(select(PersonVault.id).where(PersonVault.person_id == person.id))
    if vault_id is None:
        return None
    return await session.scalar(
        select(VaultValue.value).where(
            VaultValue.vault_id == vault_id,
            VaultValue.field_key == field_key,
            VaultValue.status.in_(("active", "pending_confirmation", "disputed")),
        )
    )


async def run_document_analysis(
    session: AsyncSession,
    settings: Settings,
    job: DocumentJob,
    *,
    storage: SupabaseStorageProvider,
    gateway: LLMGateway,
) -> None:
    rules = policy()
    doc = await session.get(Document, job.document_id)
    if doc is None or doc.deleted_at is not None:
        job.status = "failed"
        job.last_error = "document missing"
        return
    person = await session.scalar(
        select(Person).where(Person.id == doc.person_id).options(selectinload(Person.vault))
    )
    if person is None:
        job.status = "failed"
        job.last_error = "person missing"
        return
    version = None
    if job.document_version_id:
        version = await session.get(DocumentVersion, job.document_version_id)
    if version is None and doc.current_version_id:
        version = await session.get(DocumentVersion, doc.current_version_id)

    run = DocumentAnalysisRun(
        document_id=doc.id,
        document_version_id=version.id if version else None,
        document_job_id=job.id,
        pipeline_version=rules["pipeline_version"],
        classifier_version=rules["classifier_version"],
        extractor_version=rules["extractor_version"],
        normalization_version=rules["normalization_version"],
        reconciliation_policy_version=rules["reconciliation_policy_version"],
        status="running",
        current_stage="digitize",
        completed_stages=["security"],
    )
    session.add(run)
    await session.flush()
    job.analysis_run_id = run.id
    doc.status = "digitizing"

    with span("document.analysis", document_id=str(doc.id), run_id=str(run.id)):
        digitized = await _digitize(session, settings, doc, version, run, job, storage)
        text, quality, ocr_conf = digitized.text, digitized.quality, digitized.ocr_confidence
        pages = list(digitized.pages or [])
        truncated = bool(digitized.truncated)
        if len(text.strip()) < int(rules["min_text_chars"]):
            doc.status = "failed"
            doc.verification_status = "needs_review"
            run.status = "completed"
            run.error = "no_text"
            run.completed_at = datetime.now(UTC)
            job.status = "completed"
            job.last_error = "Could not read text. Upload a clearer photo or a text-based file."
            return

        _mark_stage(run, job, "classify", doc)
        filename = version.original_filename if version else doc.original_filename
        prior = doc.document_type
        hint = None if prior in set(di_taxonomy().get("generated_types") or ()) else prior
        classified = classify_document(
            filename=filename or "",
            hint=hint,
            source_type=doc.source_type,
            text=text[: int(rules.get("classify_text_chars") or 4000)],
        )
        doc.document_type = classified["document_type"]
        doc.category = classified["category"]
        doc.base_criticality = classified["base_criticality"]
        doc.trust_level = classified["trust_level"]
        doc.evidence_eligible = evidence_eligible(
            source_type=doc.source_type, document_type=doc.document_type
        )
        doc.vault_extraction_policy = "extract" if doc.evidence_eligible else "disabled"
        job.last_error = f"classified:{doc.document_type} file:{filename}"

        known = await _known_facts(session, person)
        _mark_stage(run, job, "extract", doc)
        candidates = await extract_candidates(
            gateway=gateway,
            document_id=str(doc.id),
            document_text=text,
            document_type=doc.document_type or default_type(),
            known_facts=known,
            person_id=str(person.id),
        )
        if not candidates:
            job.last_error = (job.last_error or "") + f" extract:0 fields type={doc.document_type}"
        subject_field = str(rules.get("subject_field") or "identity.full_name")
        subject_name = _candidate_str(candidates, subject_field)
        date_fields = list((rules.get("normalizers") or {}).get("date") or [])
        document_dob = _candidate_str(candidates, date_fields[0]) if date_fields else None
        known_dob = await _existing_belief(session, person, date_fields[0]) if date_fields else None
        identity = match_student(
            person,
            document_name=subject_name,
            document_type=doc.document_type or default_type(),
            document_dob=document_dob if isinstance(document_dob, str) else None,
            known_dob=str(known_dob) if known_dob else None,
        )
        doc.identity_status = identity
        _add_parties(
            session,
            doc=doc,
            version=version,
            run=run,
            candidates=candidates,
            subject_name=subject_name,
            identity=identity,
        )
        case_field = str(rules.get("identity_case_field") or "identity.subject")
        if identity == "mismatch":
            await open_case(
                session,
                person_id=person.id,
                document_id=doc.id,
                fact=None,
                field_key=case_field,
                existing_value=person.full_name or person.preferred_name,
                incoming_value=subject_name,
                case_type="wrong_subject",
                reason_code="identity_mismatch",
                severity="critical",
            )
        elif identity in set(rules.get("unconfirmed_identity") or ()):
            await open_case(
                session,
                person_id=person.id,
                document_id=doc.id,
                fact=None,
                field_key=case_field,
                existing_value=person.full_name or person.preferred_name,
                incoming_value=subject_name,
                case_type="requires_confirmation",
                reason_code="identity_unconfirmed",
                severity="high",
            )

        _mark_stage(run, job, "reconcile", doc)
        applied: list[VaultCandidate] = []
        pending_left = False
        had_critical = False
        for cand in candidates:
            if not (cand.evidence_text or "").strip():
                continue
            if not evidence_grounded(cand.evidence_text, text):
                continue
            normalized, norm_conf = normalize_field(
                cand.field_key, cand.value, document_type=doc.document_type or default_type()
            )
            authority = source_authority(doc.document_type or default_type(), cand.field_key)
            existing = await _existing_belief(session, person, cand.field_key)
            confidence = extraction_confidence(
                base=cand.confidence,
                grounded=True,
                document_quality=quality,
                ocr_confidence=ocr_conf,
                normalization_confidence=norm_conf,
            )
            fact = DocumentFact(
                person_id=person.id,
                document_id=doc.id,
                document_version_id=version.id if version else None,
                analysis_run_id=run.id,
                field_key=cand.field_key,
                raw_value=cand.value if isinstance(cand.value, (dict, list)) else cand.value,
                normalized_value=normalized,
                evidence_text=cand.evidence_text,
                page=page_for_span(cand.evidence_text, pages),
                extraction_confidence=confidence,
                normalization_confidence=norm_conf,
                ocr_confidence=ocr_conf,
                document_quality=quality,
                source_authority=authority,
                field_criticality=field_criticality(cand.field_key),
                identity_status=identity,
                sensitivity=field_sensitivity(cand.field_key),
            )
            session.add(fact)
            await session.flush()
            result = reconcile(
                ReconcileInput(
                    field_key=cand.field_key,
                    incoming_value=normalized,
                    existing_value=existing,
                    evidence_text=cand.evidence_text or "",
                    evidence_eligible=doc.evidence_eligible,
                    identity_status=identity,
                    source_authority=authority,
                    field_criticality=fact.field_criticality,
                    extraction_confidence=confidence,
                    ocr_confidence=ocr_conf,
                    document_quality=quality,
                )
            )
            fact.reconciliation_status = result.decision
            review = "pending"
            if result.decision in ("IGNORE_FOR_VAULT", "INSUFFICIENT_EVIDENCE"):
                review = "ignored"
            if result.decision in ("NEW_SAFE_FACT", "CONFIRMS_EXISTING"):
                if truncated:
                    pending_left = True
                    review = "pending"
                else:
                    applied.append(
                        VaultCandidate(
                            field_key=cand.field_key,
                            value=normalized,
                            confidence=confidence,
                            evidence_text=cand.evidence_text or "",
                            source_type="document",
                            source_reference=str(doc.id),
                            rationale_summary=result.reason,
                        )
                    )
                    review = "accepted"
            elif result.decision == "PROPOSE_UPDATE":
                pending_left = True
            elif result.decision == "CRITICAL_CONFLICT":
                pending_left = True
                had_critical = True
                await open_case(
                    session,
                    person_id=person.id,
                    document_id=doc.id,
                    fact=fact,
                    field_key=cand.field_key,
                    existing_value=existing,
                    incoming_value=normalized,
                    case_type="critical_conflict",
                    reason_code=result.reason,
                    severity="critical",
                )
            elif result.decision in ("WRONG_SUBJECT", "REQUIRES_CONFIRMATION"):
                pending_left = True
            session.add(
                DocumentCandidate(
                    document_id=doc.id,
                    document_version_id=version.id if version else None,
                    document_job_id=job.id,
                    person_id=person.id,
                    field_key=cand.field_key,
                    value=normalized if isinstance(normalized, (dict, list)) else normalized,
                    confidence=confidence,
                    evidence_text=cand.evidence_text,
                    review_status=review,
                    reasoning_summary=f"{result.decision}:{result.reason}",
                )
            )

        if applied and identity in set(rules.get("auto_apply_identity") or ("matched",)) and not truncated:
            await accept_vault_candidates(
                session, person, applied, from_document=True, already_reconciled=True,
                apply_order=list(policy().get("apply_order") or []),
            )

        pending_left = pending_left or truncated
        if identity == "mismatch":
            doc.verification_status = "identity_mismatch"
        elif had_critical:
            doc.verification_status = "conflict"
        elif pending_left:
            doc.verification_status = "needs_review"
        else:
            doc.verification_status = "unverified"
        doc.status = "awaiting_review" if pending_left else "processed"
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        _mark_stage(run, job, "completed")
        job.status = "completed"
        from pai.domains.journey.service import record_document_processed

        record_document_processed(
            session, person.id, document_id=doc.id, filename=doc.original_filename or "file"
        )


async def _digitize(
    session: AsyncSession,
    settings: Settings,
    doc: Document,
    version: DocumentVersion | None,
    run: DocumentAnalysisRun,
    job: DocumentJob,
    storage: SupabaseStorageProvider,
) -> DigitizationResult:
    _mark_stage(run, job, "digitize", doc)
    min_chars = int(policy()["min_text_chars"])
    if version is not None and version.content_text and len(version.content_text.strip()) >= min_chars:
        run.digitization = {"method": "cached", "quality": "good"}
        run.ocr_provider = "native"
        text = version.content_text
        return DigitizationResult(
            text=text,
            method="cached",
            provider="native",
            quality="good",
            pages=[{"page": 1, "text": text}],
        )
    prior = await session.scalar(
        select(DocumentAnalysisRun.digitization)
        .where(
            DocumentAnalysisRun.document_version_id == (version.id if version else None),
            DocumentAnalysisRun.status == "completed",
            DocumentAnalysisRun.digitization.is_not(None),
        )
        .order_by(DocumentAnalysisRun.completed_at.desc())
        .limit(1)
    )
    if isinstance(prior, dict) and (prior.get("text") or "").strip():
        run.digitization = {"method": "prior_run", "quality": prior.get("quality")}
        text = str(prior["text"])
        return DigitizationResult(
            text=text,
            method="prior_run",
            provider=str(prior.get("provider") or "native"),
            quality=str(prior.get("quality") or "unknown"),
            ocr_confidence=prior.get("ocr_confidence"),
            truncated=bool(prior.get("truncated")),
            pages=list(prior.get("pages") or [{"page": 1, "text": text}]),
        )

    path = version.storage_path if version is not None else doc.storage_path
    mime = version.mime_type if version is not None else doc.mime_type
    filename = version.original_filename if version is not None else doc.original_filename
    raw = await storage.download_bytes(path)
    artifact = None
    if version is not None:
        artifact = f"{doc.person_id}/{doc.id}/{version.id}/ocr.{settings.document_ocr_provider}.v1.json"
    result = await digitize_bytes(
        raw,
        mime_type=mime,
        filename=filename,
        settings=settings,
        storage=storage,
        artifact_path=artifact,
    )
    if version is not None and result.text:
        version.content_text = result.text[: int(policy().get("max_content_text_chars") or 50000)]
    run.ocr_provider = result.provider
    run.ocr_model = result.model
    run.provider_artifact_path = artifact if result.provider != "native" else None
    run.digitization = result.model_dump(exclude={"raw_response"})
    return result


def _candidate_value(candidates: list[VaultCandidate], field_key: str):
    for cand in candidates:
        if cand.field_key == field_key and cand.value not in (None, ""):
            return cand.value
    return None


def _candidate_str(candidates: list[VaultCandidate], field_key: str) -> str | None:
    value = _candidate_value(candidates, field_key)
    return value.strip() if isinstance(value, str) else None


def _issuer_name(candidates: list[VaultCandidate]) -> str | None:
    for spec in policy().get("issuer_from") or []:
        value = _candidate_value(candidates, str(spec.get("field") or ""))
        if isinstance(value, dict):
            inst = value.get(spec.get("path") or "institution")
            if isinstance(inst, str) and inst.strip():
                return inst.strip()
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _add_parties(
    session,
    *,
    doc: Document,
    version: DocumentVersion | None,
    run: DocumentAnalysisRun,
    candidates: list[VaultCandidate],
    subject_name: str | None,
    identity: str,
) -> None:
    roles = expected_roles(doc.document_type or default_type())
    session.add(
        DocumentParty(
            document_id=doc.id,
            document_version_id=version.id if version else None,
            analysis_run_id=run.id,
            role=roles[0],
            display_name=subject_name,
            identity_status=identity,
        )
    )
    issuer = _issuer_name(candidates)
    if issuer and "issuer" in roles:
        session.add(
            DocumentParty(
                document_id=doc.id,
                document_version_id=version.id if version else None,
                analysis_run_id=run.id,
                role="issuer",
                display_name=issuer,
                identity_status="not_applicable",
            )
        )
