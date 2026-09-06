from __future__ import annotations

from pai.intelligences.documents.classification.taxonomy import classify_from_name, type_meta


def classify_document(*, filename: str, hint: str | None, source_type: str, text: str = "") -> dict:
    kind = classify_from_name(filename, hint, text)
    meta = type_meta(kind)
    category = "generated" if source_type == "ai_generated" else meta["category"]
    return {
        "document_type": kind,
        "category": category,
        "base_criticality": meta["base_criticality"],
        "trust_level": "pai_generated" if source_type == "ai_generated" else meta["trust_level"],
    }
