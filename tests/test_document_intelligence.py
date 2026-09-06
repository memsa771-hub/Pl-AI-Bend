from pai.intelligences.documents.classification.taxonomy import classify_from_name, evidence_eligible
from pai.intelligences.documents.identity.names import names_match
from pai.intelligences.documents.reconciliation.engine import ReconcileInput, reconcile
from pai.intelligences.documents.security.validation import sniff_mime


def test_generated_docs_are_not_evidence():
    assert evidence_eligible(source_type="ai_generated", document_type="resume") is False
    assert evidence_eligible(source_type="onboarding", document_type="sop") is False
    assert evidence_eligible(source_type="document_vault", document_type="transcript") is True
    assert evidence_eligible(source_type="ai_generated", document_type="other") is False


def test_identity_mismatch_is_deterministic():
    assert names_match("Musawir Khan", "Musawir Khan") == "matched"
    assert names_match("Musawir Khan", "Ahmed Khan") == "mismatch"


def test_gpa_critical_conflict_does_not_auto_apply():
    result = reconcile(
        ReconcileInput(
            field_key="education.gpa",
            incoming_value={"value": 3.5, "scale": 4.0, "type": "cumulative"},
            existing_value={"value": 2.5, "scale": 4.0, "type": "cumulative"},
            evidence_text="CGPA: 3.50 / 4.00",
            source_authority="high",
            field_criticality="critical",
            extraction_confidence=0.97,
            document_quality="good",
        )
    )
    assert result.decision == "CRITICAL_CONFLICT"


def test_new_safe_non_critical_can_apply():
    result = reconcile(
        ReconcileInput(
            field_key="career.skills",
            incoming_value=[{"name": "Python"}],
            existing_value=None,
            evidence_text="Skills: Python",
            source_authority="medium",
            field_criticality="normal",
            extraction_confidence=0.95,
            document_quality="good",
        )
    )
    assert result.decision == "NEW_SAFE_FACT"


def test_no_evidence_span_is_insufficient():
    result = reconcile(
        ReconcileInput(
            field_key="education.gpa",
            incoming_value=3.5,
            evidence_text="",
            source_authority="high",
            field_criticality="critical",
            extraction_confidence=0.99,
        )
    )
    assert result.decision == "INSUFFICIENT_EVIDENCE"


def test_sniff_rejects_spoofed_pdf_header():
    assert sniff_mime(b"%PDF-1.4 hello", "x.pdf") == "application/pdf"
    assert sniff_mime(b"\xff\xd8\xff\xdb", "x.jpg") == "image/jpeg"
    assert sniff_mime(b"just text about a cv", "cv.txt") == "text/plain"


def test_likely_match_cannot_auto_apply():
    result = reconcile(
        ReconcileInput(
            field_key="career.skills",
            incoming_value=[{"name": "Python"}],
            existing_value=None,
            evidence_text="Skills: Python",
            source_authority="medium",
            identity_status="likely_match",
            field_criticality="normal",
            extraction_confidence=0.95,
            document_quality="good",
        )
    )
    assert result.decision == "REQUIRES_CONFIRMATION"


def test_classify_uses_ocr_text_when_filename_is_generic():
    from pai.intelligences.documents.classification.taxonomy import classify_from_name
    from pai.intelligences.documents.classification.taxonomy import type_meta

    assert classify_from_name("passport-scan.png") == "passport"
    assert classify_from_name("scan.jpg") == "other"
    assert classify_from_name("scan.jpg", text="Republic of Pakistan Passport") == "passport"
    assert classify_from_name("scan.jpg", hint="resume", text="Official Transcript CGPA") == "resume"
    assert classify_from_name("scan.jpg", text="Official Transcript CGPA") == "transcript"
    assert classify_from_name("my-sop.pdf") == "sop"
    assert classify_from_name("x.pdf", text="statement of purpose") != "sop"
    assert type_meta("transcript")["extractor"] == "transcript"
    assert type_meta("lor")["party_roles"] == ["subject", "author"]


def test_transcript_gpa_shape_attaches_to_education_payload():
    from pai.domains.student.typed_apply import _education_payload

    payload = _education_payload({"value": 3.5, "scale": 4.0, "type": "cumulative"})
    assert payload is not None
    assert payload["gpa"] == 3.5
    assert payload["gpa_scale"] == 4.0
    assert "institution" not in payload


def test_openai_vision_is_the_ocr_provider():
    from types import SimpleNamespace

    from pai.intelligences.documents.providers.factory import ocr_provider
    from pai.intelligences.documents.providers.openai_vision import (
        OpenAIVisionProvider,
        pages_for_vision,
    )

    settings = SimpleNamespace(
        openai_api_key="sk-test",
        openai_base_url="https://api.openai.com/v1",
        document_ocr_provider="openai_vision",
        llm_document_vision_model="gpt-4o-mini",
        document_vision_max_pages=20,
        document_vision_batch_pages=2,
        document_processing_timeout_seconds=180,
        llm_timeout_seconds=60,
    )
    provider = ocr_provider(settings)
    assert isinstance(provider, OpenAIVisionProvider)
    assert provider.configured() is True
    jpeg = b"\xff\xd8\xff" + b"\x00" * 9000
    parts, total = pages_for_vision(jpeg, "image/jpeg", max_pages=20)
    assert parts == [(1, "image/jpeg", jpeg)]
    assert total == 1
    native = ocr_provider(SimpleNamespace(document_ocr_provider="native"))
    assert native.name == "native"
    settings.openai_api_key = ""
    assert OpenAIVisionProvider(settings).configured() is False


def test_pdf_pages_are_rasterized_and_not_silently_truncated():
    import fitz

    from pai.intelligences.documents.providers.openai_vision import pages_for_vision

    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "Page one CGPA 3.50")
    pdf.new_page().insert_text((72, 72), "Page two graduation")
    data = pdf.tobytes()
    pdf.close()
    images, total = pages_for_vision(data, "application/pdf", max_pages=1)
    assert total == 2
    assert len(images) == 1
    assert images[0][0] == 1
    assert images[0][1] == "image/jpeg"
    assert images[0][2][:2] == b"\xff\xd8"
    full, full_total = pages_for_vision(data, "application/pdf", max_pages=20)
    assert full_total == 2
    assert [page for page, _, _ in full] == [1, 2]


def test_evidence_must_appear_in_digitized_text():
    from pai.intelligences.documents.evidence.grounding import evidence_grounded, page_for_span

    text = "Student Name: Musawir Khan\nCGPA: 2.50 / 4.00"
    assert evidence_grounded("CGPA: 2.50 / 4.00", text) is True
    assert evidence_grounded("CGPA  2.50/4.00", text) is True
    assert evidence_grounded("CGPA: 3.50 / 4.00", text) is False
    assert page_for_span("CGPA: 2.50 / 4.00", [{"page": 2, "text": text}]) == 2


def test_unreadable_ocr_never_applies_to_vault():
    result = reconcile(
        ReconcileInput(
            field_key="career.skills",
            incoming_value=[{"name": "Python"}],
            existing_value=None,
            evidence_text="Skills: Python",
            source_authority="medium",
            field_criticality="normal",
            extraction_confidence=0.95,
            document_quality="unreadable",
        )
    )
    assert result.decision == "INSUFFICIENT_EVIDENCE"


def test_low_quality_ocr_cannot_auto_apply():
    result = reconcile(
        ReconcileInput(
            field_key="career.skills",
            incoming_value=[{"name": "Python"}],
            existing_value=None,
            evidence_text="Skills: Python",
            source_authority="medium",
            field_criticality="normal",
            extraction_confidence=0.95,
            document_quality="low",
        )
    )
    assert result.decision != "NEW_SAFE_FACT"
    assert result.decision == "PROPOSE_UPDATE"


def test_counselor_profile_surfaces_critical_verification():
    from pai.intelligences.counselor.context import CounselorContext

    ctx = CounselorContext(
        person_id="p1",
        goal="MS CS",
        critical_verifications=[
            {
                "fieldKey": "education.gpa",
                "existingValue": {"value": 2.5, "scale": 4.0},
                "incomingValue": {"value": 3.5, "scale": 4.0},
            }
        ],
    )
    block = ctx.profile_block()
    assert block.startswith("CRITICAL VERIFICATION:")
    assert "education.gpa disputed" in block
    assert "Do not make GPA-sensitive recommendations until resolved" in block
    assert "Ask the student to resolve this" in block


def test_missing_page_markers_do_not_fabricate_provenance():
    from pai.intelligences.documents.evidence.grounding import page_for_span
    from pai.intelligences.documents.providers.openai_vision import _merge_usage, _split_pages

    marked = _split_pages("===PAGE 1===\nAlpha\n===PAGE 2===\nCGPA 3.50\n", [1, 2])
    assert marked == [{"page": 1, "text": "Alpha"}, {"page": 2, "text": "CGPA 3.50"}]
    assert page_for_span("CGPA 3.50", marked) == 2

    unmarked = _split_pages("Alpha\nCGPA 3.50 on page two", [1, 2])
    assert unmarked == [{"page": None, "text": "Alpha\nCGPA 3.50 on page two"}]
    assert page_for_span("CGPA 3.50", unmarked) is None

    usage: dict = {}
    _merge_usage(usage, {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})
    _merge_usage(usage, {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11})
    assert usage == {"prompt_tokens": 18, "completion_tokens": 7, "total_tokens": 25}
