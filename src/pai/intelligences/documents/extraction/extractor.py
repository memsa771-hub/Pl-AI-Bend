from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from pai.platform.llm.gateway import LLMGateway
from pai.platform.llm.schemas import LLMMessage
from pai.intelligences.counselor.agents import FactExtractionAgent
from pai.kernel.contracts.schemas import VaultCandidate
from pai.intelligences.documents.classification.taxonomy import type_meta
from pai.intelligences.documents.config import policy
from pai.intelligences.documents.evidence.grounding import (
    evidence_grounded,
    value_supported_by_evidence,
)
from pai.intelligences.documents.extraction.schemas import degree as degree_schema
from pai.intelligences.documents.extraction.schemas import passport as passport_schema
from pai.intelligences.documents.extraction.schemas import resume as resume_schema
from pai.intelligences.documents.extraction.schemas import test_score as test_schema
from pai.intelligences.documents.extraction.schemas import transcript as transcript_schema

logger = logging.getLogger(__name__)


class DocumentExtractionResult(BaseModel):
    structured_payload: dict
    candidates: list[VaultCandidate]


class GenericSemanticExtraction(BaseModel):
    summary: str = ""
    entities: list[dict[str, Any]] = Field(default_factory=list)
    dates: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)


# Schema modules stay in code; which type uses which schema lives in taxonomy.json.
_SCHEMAS: dict[str, tuple[type[BaseModel], object]] = {
    "passport": (passport_schema.PassportExtraction, passport_schema.to_field_map),
    "transcript": (transcript_schema.TranscriptExtraction, transcript_schema.to_field_map),
    "degree": (degree_schema.DegreeExtraction, degree_schema.to_field_map),
    "resume": (resume_schema.ResumeExtraction, resume_schema.to_field_map),
    "test_score": (test_schema.TestScoreExtraction, test_schema.to_field_map),
}


async def extract_candidates(
    *,
    gateway: LLMGateway,
    document_id: str,
    document_text: str,
    document_type: str,
    known_facts: list[str],
    person_id: str,
    digitization_truncated: bool = False,
) -> DocumentExtractionResult:
    limit = int(policy().get("extract_char_limit") or 20000)
    chunks = _chunks(document_text, limit)
    typed_parts = [
        await _try_typed(
            gateway,
            document_text=chunk,
            grounding_text=document_text,
            document_type=document_type,
            document_id=document_id,
        )
        for chunk in chunks
    ]
    typed_candidates = _dedupe_candidates(
        [candidate for part in typed_parts if part for candidate in part[1]]
    )
    payloads = [part[0].model_dump(exclude_none=True) for part in typed_parts if part]
    minimum = int(policy().get("typed_min_grounded_fields") or 1)
    if len(typed_candidates) >= minimum:
        return DocumentExtractionResult(
            structured_payload=_structured_envelope(
                document_id=document_id,
                document_type=document_type,
                extraction=payloads[0] if len(payloads) == 1 else {"segments": payloads},
                candidates=typed_candidates,
                truncated=digitization_truncated,
            ),
            candidates=typed_candidates,
        )
    try:
        agent = FactExtractionAgent(gateway)
        fallback_parts = [
            await agent.extract_from_document(
                document_id=document_id,
                document_text=chunk,
                document_type_hint=document_type,
                known_facts=known_facts,
                person_id=person_id,
            )
            for chunk in chunks
        ]
        grounded = _dedupe_candidates([*typed_candidates, *[
            row for part in fallback_parts for row in part
            if evidence_grounded(row.evidence_text, document_text)
        ]])
        semantic = await _generic_semantic(gateway, chunks)
        return DocumentExtractionResult(
            structured_payload=_structured_envelope(
                document_id=document_id,
                document_type=document_type,
                extraction={
                    "typed": payloads[0] if len(payloads) == 1 else {"segments": payloads},
                    "semantic": semantic,
                    "vaultFactProjection": [
                        {"field": row.field_key, "value": row.value}
                        for row in grounded
                    ]
                },
                candidates=grounded,
                truncated=digitization_truncated,
            ),
            candidates=grounded,
        )
    except Exception:
        logger.exception("Omnibus document extract failed type=%s", document_type)
        raise


async def _try_typed(
    gateway: LLMGateway,
    *,
    document_text: str,
    grounding_text: str,
    document_type: str,
    document_id: str,
) -> tuple[BaseModel, list[VaultCandidate]] | None:
    spec = _SCHEMAS.get(str(type_meta(document_type).get("extractor") or ""))
    if spec is None:
        return None
    schema, mapper = spec
    rules = policy()
    try:
        out = await gateway.run(
            task="document_extract",
            messages=[
                LLMMessage(
                    role="system",
                    content=(
                        "Extract only fields present in the document. "
                        "Every extracted field has its own evidence property containing a verbatim span. "
                        "Do not reuse unrelated evidence and do not invent facts."
                    ),
                ),
                LLMMessage(role="user", content=document_text),
            ],
            output_schema=schema,
            temperature=0.0,
        )
    except Exception:
        logger.exception("Typed document extract failed type=%s", document_type)
        return None
    if not isinstance(out, BaseModel):
        return None
    base = float(rules.get("typed_confidence") or 0.9)
    candidates: list[VaultCandidate] = []
    for field_key, value, evidence in mapper(out):
        span = evidence if evidence_grounded(evidence, grounding_text) else None
        if span is None and isinstance(value, str) and evidence_grounded(value, grounding_text):
            span = value
        if not span or not value_supported_by_evidence(value, span):
            continue
        candidates.append(
            VaultCandidate(
                field_key=field_key,
                value=value,
                confidence=base,
                evidence_text=span,
                source_type="document",
                source_reference=document_id,
            )
        )
    return out, candidates


def _structured_envelope(
    *,
    document_id: str,
    document_type: str,
    extraction: dict,
    candidates: list[VaultCandidate],
    truncated: bool,
) -> dict:
    extractor = str(type_meta(document_type).get("extractor") or document_type or "generic")
    subject = next(
        (row.value for row in candidates if row.field_key == "identity.full_name"),
        None,
    )
    evidence = [
        {
            "field": row.field_key,
            "sourceSpan": row.evidence_text,
            "confidence": row.confidence,
        }
        for row in candidates
        if (row.evidence_text or "").strip()
    ]
    return {
        "documentId": document_id,
        "documentType": document_type,
        "schemaVersion": f"{extractor}.v1",
        "extractionComplete": not truncated,
        "truncated": truncated,
        "subject": {"name": subject} if subject else {},
        "extraction": extraction,
        "evidence": evidence,
    }


def _chunks(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    overlap = min(1000, max(100, limit // 10))
    return [text[start:start + limit] for start in range(0, len(text), limit - overlap)]


def _dedupe_candidates(rows: list[VaultCandidate]) -> list[VaultCandidate]:
    unique: dict[tuple[str, str, str], VaultCandidate] = {}
    for row in rows:
        key = (row.field_key, repr(row.value), row.evidence_text)
        unique.setdefault(key, row)
    return list(unique.values())


async def _generic_semantic(gateway: LLMGateway, chunks: list[str]) -> dict:
    segments: list[dict] = []
    for chunk in chunks:
        try:
            result = await gateway.run(
                task="document_extract",
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Represent this document segment semantically without inventing facts. "
                            "Preserve headings, entities, dates, tables, original terminology, and a concise summary."
                        ),
                    ),
                    LLMMessage(role="user", content=chunk),
                ],
                output_schema=GenericSemanticExtraction,
                temperature=0.0,
            )
        except Exception:
            logger.exception("Generic semantic extraction failed for one segment")
            continue
        if isinstance(result, GenericSemanticExtraction):
            segments.append(result.model_dump(exclude_none=True))
    return segments[0] if len(segments) == 1 else {"segments": segments}
