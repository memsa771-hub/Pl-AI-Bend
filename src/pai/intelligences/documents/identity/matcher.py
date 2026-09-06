from __future__ import annotations

from pai.intelligences.documents.classification.taxonomy import evidence_eligible
from pai.intelligences.documents.config import policy
from pai.intelligences.documents.identity.names import names_match
from pai.intelligences.documents.normalization.dates import parse_date
from pai.domains.student.person.models import Person


def match_student(
    person: Person,
    *,
    document_name: str | None,
    document_type: str,
    document_dob: str | None = None,
    known_dob: str | None = None,
) -> str:
    if not evidence_eligible(source_type="document_vault", document_type=document_type):
        return "not_applicable"
    known = person.full_name or person.preferred_name
    if not document_name or not known:
        name_status = "ambiguous" if document_name or known else "not_applicable"
    else:
        name_status = names_match(known, document_name)
    date_fields = set((policy().get("normalizers") or {}).get("date") or [])
    if date_fields:
        left, right = parse_date(document_dob), parse_date(known_dob)
        if left and right:
            if left != right:
                return "mismatch"
            if name_status in set(policy().get("unconfirmed_identity") or ()):
                return "matched"
    return name_status
