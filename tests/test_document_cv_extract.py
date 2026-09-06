"""CV/document text extraction — PDF and DOCX must yield real text, not a placeholder."""

from __future__ import annotations

import io
import zipfile

from pai.domains.documents.text import extract_text_from_bytes
from pai.kernel.contracts.schemas import VaultCandidate
from pai.kernel.policy.verifier import policy_decision, validate_candidate
from pai.domains.student.vault.catalog import extraction_catalog_hint, get_catalog_field


def _docx_with_text(text: str) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>"
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
        )
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_extracts_plain_text_and_docx():
    assert "hello vault" in extract_text_from_bytes(
        b"hello vault", "text/plain", "cv.txt"
    )
    docx = _docx_with_text("Aisha Khan, A-Levels, NYU Abu Dhabi intern")
    text = extract_text_from_bytes(
        docx,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "cv.docx",
    )
    assert "Aisha Khan" in text
    assert "NYU Abu Dhabi" in text


def test_binary_placeholder_is_gone():
    empty = extract_text_from_bytes(b"%PDF-1.4 junk", "application/pdf", "scan.pdf")
    assert "[binary document" not in empty


def test_catalog_tells_llm_about_career_writes():
    hint = extraction_catalog_hint()
    assert "career.work_history" in hint
    assert "career.skills" in hint
    assert "memory.observed" in hint
    skills = get_catalog_field("career.skills")
    assert skills is not None
    assert skills.editable is True
    assert skills.derived is False


def test_cv_career_candidates_validate():
    skill = VaultCandidate(
        field_key="career.skills",
        value=[{"name": "Python"}, {"name": "SQL"}],
        confidence=0.92,
        evidence_text="Skills: Python, SQL",
        source_reference="doc-1",
        source_type="document",
    )
    job = VaultCandidate(
        field_key="career.work_history",
        value={"organization": "Acme", "title": "Intern"},
        confidence=0.9,
        evidence_text="Intern at Acme",
        source_reference="doc-1",
        source_type="document",
    )
    assert validate_candidate(skill) is not None
    assert validate_candidate(job) is not None
    assert policy_decision(skill, from_document=True) == "accept"


def test_omnibus_cv_prompt_asks_for_full_resume():
    from pai.intelligences.vault.llm_extractor import _render

    text = _render(
        "omnibus.v1.jinja2",
        source="document",
        source_reference="doc-1",
        document_type_hint="resume",
        known_facts=[],
        catalog_hint=extraction_catalog_hint(),
        text="CV body",
    )
    assert "This source is a CV/resume" in text
    assert "career.work_history" in text
    assert '["FAST"' not in text
