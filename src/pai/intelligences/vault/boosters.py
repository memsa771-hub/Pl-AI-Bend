"""Deterministic high-precision extractors — never miss clear GPA/marks/countries."""

from __future__ import annotations

import re
import uuid
from typing import Any

from pai.domains.student.normalization.geo import extract_countries_from_text
from pai.domains.student.normalization.vocab import BudgetBand
from pai.kernel.contracts.schemas import VaultCandidate

_MARKS = re.compile(
    r"\b(\d{2,4})\s*/\s*(\d{2,4})\b",
    re.I,
)
_CGPA = re.compile(
    r"\b(?:cgpa|gpa|c\.?g\.?p\.?a\.?)\s*(?:is|was|of|=|:)?\s*(\d(?:\.\d{1,2})?)\b",
    re.I,
)
_CGPA_BARE = re.compile(
    r"\b(\d\.\d{1,2})\s*(?:/\s*4(?:\.0)?)?\b",
)
_STREAM = re.compile(
    r"\b(pre[\s-]?medical|pre[\s-]?engineering|fsc|fa\b|ics|bscs|bcss|bsc|bs\s*cs|"
    r"computer\s+science|software\s+engineering|"
    r"a[\s-]?levels?|igcse|gcse|cbse|international\s+baccalaureate|ib\s*(?:dp|diploma))\b",
    re.I,
)
_CITY = re.compile(
    r"\b(islamabad|lahore|karachi|peshawar|rawalpindi|multan|faisalabad|"
    r"dubai|sharjah|abu\s+dhabi|doha|riyadh|jeddah|"
    r"berlin|munich|shanghai|beijing|toronto|london|"
    r"new\s+york|singapore|sydney|melbourne|paris|amsterdam)\b",
    re.I,
)
# ponytail: high-precision aliases when the student names them — not a world catalog.
_UNI = re.compile(
    r"\b(FAST|NUST|GIKI|UET|LUMS|IBA|COMSATS|Bahria|PIEAS|"
    r"NYUAD|NYU\s*Abu\s*Dhabi|Khalifa|"
    r"TU\s*Munich|TUM|RWTH|Tsinghua|Peking|Oxford|Cambridge|MIT|Stanford|ETH|NUS)\b",
    re.I,
)
_FUNDED = re.compile(r"\b(fully\s+funded|scholarship|daad|csc|limited\s+budget|low\s+budget)\b", re.I)
_ADDITIONAL_MATHS = re.compile(r"\badditional\s+maths?\b", re.I)


def _cand(
    field_key: str,
    value: Any,
    *,
    evidence: str,
    source_reference: str,
    source_type: str,
    confidence: float = 0.93,
    rationale: str = "deterministic_booster",
) -> VaultCandidate:
    return VaultCandidate(
        candidate_id=str(uuid.uuid4()),
        field_key=field_key,
        value=value,
        confidence=confidence,
        explicitness="explicit",
        source_type=source_type,  # type: ignore[arg-type]
        source_reference=source_reference,
        evidence_text=evidence[:500],
        is_correction=False,
        requires_confirmation=False,
        rationale_summary=rationale,
    )


def run_deterministic_boosters(
    text: str,
    *,
    source_reference: str,
    source_type: str = "chat",
) -> tuple[list[VaultCandidate], list[str]]:
    """Return (candidates, hit labels). High precision only."""
    raw = text or ""
    hits: list[str] = []
    out: list[VaultCandidate] = []
    if len(raw.strip()) < 2:
        return out, hits

    # Marks 877/1100
    for m in _MARKS.finditer(raw):
        obtained, total = int(m.group(1)), int(m.group(2))
        if total <= 0 or obtained > total * 1.05:
            continue
        evidence = m.group(0)
        out.append(
            _cand(
                "education.marks",
                {"obtained": obtained, "total": total},
                evidence=evidence,
                source_reference=source_reference,
                source_type=source_type,
                rationale="booster:marks_ratio",
            )
        )
        hits.append("education.marks")
        # Also enrich program if stream nearby
        window_start = max(0, m.start() - 80)
        window = raw[window_start : m.end() + 40]
        stream_m = _STREAM.search(window)
        if stream_m:
            stream = _normalize_stream(stream_m.group(1))
            out.append(
                _cand(
                    "education.stream",
                    stream,
                    evidence=stream_m.group(0),
                    source_reference=source_reference,
                    source_type=source_type,
                    rationale="booster:stream_near_marks",
                )
            )
            degree = "FSc" if "pre" in stream.lower() or stream.lower() in ("fsc",) else stream
            major = stream if "pre" in stream.lower() else None
            payload: dict[str, Any] = {
                "degree": degree,
                "marks_obtained": obtained,
                "marks_total": total,
            }
            if major:
                payload["major"] = major
            out.append(
                _cand(
                    "education.program",
                    payload,
                    evidence=window.strip()[:240],
                    source_reference=source_reference,
                    source_type=source_type,
                    rationale="booster:program_from_marks_context",
                )
            )
            hits.append("education.program")

    # CGPA / GPA
    cgpa_m = _CGPA.search(raw)
    if cgpa_m:
        gpa = float(cgpa_m.group(1))
        if 0 < gpa <= 4.0:
            out.append(
                _cand(
                    "education.gpa",
                    {"gpa": gpa, "gpa_scale": 4.0, "degree": "bachelor"},
                    evidence=cgpa_m.group(0),
                    source_reference=source_reference,
                    source_type=source_type,
                    rationale="booster:cgpa",
                )
            )
            hits.append("education.gpa")
    elif re.search(r"\b(?:cgpa|gpa)\b", raw, re.I):
        bare = _CGPA_BARE.search(raw)
        if bare:
            gpa = float(bare.group(1))
            if 0 < gpa <= 4.0:
                out.append(
                    _cand(
                        "education.gpa",
                        {"gpa": gpa, "gpa_scale": 4.0},
                        evidence=bare.group(0),
                        source_reference=source_reference,
                        source_type=source_type,
                        confidence=0.88,
                        rationale="booster:cgpa_bare",
                    )
                )
                hits.append("education.gpa")

    # Degree / stream mentions without marks
    if "education.stream" not in hits:
        stream_m = _STREAM.search(raw)
        if stream_m:
            stream = _normalize_stream(stream_m.group(1))
            out.append(
                _cand(
                    "education.stream",
                    stream,
                    evidence=stream_m.group(0),
                    source_reference=source_reference,
                    source_type=source_type,
                    confidence=0.9,
                    rationale="booster:stream",
                )
            )
            hits.append("education.stream")
            if stream.upper() in ("BSCS", "BCSS", "BS CS") or "computer science" in stream.lower():
                out.append(
                    _cand(
                        "education.program",
                        {"degree": stream.upper() if len(stream) <= 8 else "BS", "major": "Computer Science"},
                        evidence=stream_m.group(0),
                        source_reference=source_reference,
                        source_type=source_type,
                        confidence=0.9,
                        rationale="booster:cs_program",
                    )
                )
                hits.append("education.program")

    countries = extract_countries_from_text(raw)
    if countries:
        value = ", ".join(countries)
        out.append(
            _cand(
                "application.study_country",
                value,
                evidence=value,
                source_reference=source_reference,
                source_type=source_type,
                rationale="booster:study_country",
            )
        )
        out.append(
            _cand(
                "mobility.preferred_regions",
                countries,
                evidence=value,
                source_reference=source_reference,
                source_type=source_type,
                confidence=0.9,
                rationale="booster:preferred_regions",
            )
        )
        hits.append("application.study_country")

    city_m = _CITY.search(raw)
    if city_m:
        out.append(
            _cand(
                "location.current_city",
                city_m.group(1).title(),
                evidence=city_m.group(0),
                source_reference=source_reference,
                source_type=source_type,
                confidence=0.88,
                rationale="booster:city",
            )
        )
        hits.append("location.current_city")

    unis = []
    for m in _UNI.finditer(raw):
        name = m.group(1).strip()
        if name not in unis:
            unis.append(name)
    if unis:
        out.append(
            _cand(
                "application.target_universities",
                unis,
                evidence=", ".join(unis),
                source_reference=source_reference,
                source_type=source_type,
                confidence=0.9,
                rationale="booster:target_universities",
            )
        )
        hits.append("application.target_universities")

    if _ADDITIONAL_MATHS.search(raw):
        out.append(
            _cand(
                "education.additional_maths",
                True,
                evidence="Additional Maths",
                source_reference=source_reference,
                source_type=source_type,
                rationale="booster:additional_maths",
            )
        )
        hits.append("education.additional_maths")

    funded = _FUNDED.search(raw)
    if funded:
        phrase = funded.group(1).lower()
        if "scholarship" in phrase or "funded" in phrase or "daad" in phrase or "csc" in phrase:
            out.append(
                _cand(
                    "finance.scholarship_interest",
                    True,
                    evidence=funded.group(0),
                    source_reference=source_reference,
                    source_type=source_type,
                    rationale="booster:scholarship_interest",
                )
            )
            hits.append("finance.scholarship_interest")
        if "budget" in phrase:
            out.append(
                _cand(
                    "finance.funding_status",
                    BudgetBand.limited.value,
                    evidence=funded.group(0),
                    source_reference=source_reference,
                    source_type=source_type,
                    rationale="booster:funding_status",
                )
            )
            hits.append("finance.funding_status")

    # Career goal signals
    if re.search(r"\b(ms|master'?s?)\b.*\b(cs|ai|cyber|computer\s+science|machine\s+learning)\b", raw, re.I) or re.search(
        r"\b(cs|ai|cyber|ml)\b.*\b(ms|master'?s?)\b", raw, re.I
    ):
        goal = "MS in CS / AI / Cyber"
        if re.search(r"\bai\b|artificial\s+intelligence", raw, re.I):
            goal = "MS in AI / Computer Science"
        if re.search(r"\bcyber\b", raw, re.I):
            goal = "MS in CS, AI, or Cyber"
        out.append(
            _cand(
                "application.career_interest",
                goal,
                evidence=goal,
                source_reference=source_reference,
                source_type=source_type,
                confidence=0.9,
                rationale="booster:career_interest",
            )
        )
        hits.append("application.career_interest")

    # Internship / work experience — high-precision only when duration + role/org cues appear
    work = re.search(
        r"\b(\d+)\s*[- ]?\s*month(?:s)?\s+(?:software\s+)?(?:engineering\s+)?"
        r"(internship|intern)\b"
        r"(?:\s+at\s+([A-Za-z0-9][\w&.\'-]{0,40}?))?"
        r"(?=\s+(?:and|but|plus|also|,|;|\.|$)|$)",
        raw,
        re.I,
    )
    if work:
        months = work.group(1)
        org = (work.group(3) or "local company").strip(" .,")
        evidence = work.group(0).strip()
        out.append(
            _cand(
                "career.work_history",
                {
                    "organization": org[:256],
                    "title": f"{months}-month software internship",
                    "employment_type": "internship",
                    "description": evidence[:500],
                },
                evidence=evidence,
                source_reference=source_reference,
                source_type=source_type,
                confidence=0.92,
                rationale="booster:work_internship",
            )
        )
        hits.append("career.work_history")

    # Language / cert exams named explicitly (CILS, CELI, IELTS already via other paths)
    cert = re.search(
        r"\b(CILS|CELI|TOEFL|GRE|GMAT)\b(?:\s*(B[12]|C[12]|[67]\.?\d))?",
        raw,
        re.I,
    )
    if cert:
        name = cert.group(1).upper()
        level = (cert.group(2) or "").strip()
        label = f"{name} {level}".strip() if level else name
        if name in ("CILS", "CELI"):
            out.append(
                _cand(
                    "career.certifications",
                    {"name": label, "issuer": "Italian language exam"},
                    evidence=cert.group(0),
                    source_reference=source_reference,
                    source_type=source_type,
                    confidence=0.92,
                    rationale="booster:language_cert",
                )
            )
            hits.append("career.certifications")

    # Named software/ML projects
    proj = re.search(
        r"\b(flutter\s+app|machine\s+learning\s+classifier|ml\s+classifier|"
        r"rest\s+apis?|open[\s-]?source\s+project)\b",
        raw,
        re.I,
    )
    if proj:
        out.append(
            _cand(
                "career.projects",
                {
                    "name": proj.group(1).strip()[:256],
                    "description": proj.group(0)[:500],
                },
                evidence=proj.group(0),
                source_reference=source_reference,
                source_type=source_type,
                confidence=0.9,
                rationale="booster:project_mention",
            )
        )
        hits.append("career.projects")

    return out, hits


def _normalize_stream(raw: str) -> str:
    t = re.sub(r"\s+", " ", raw.strip())
    low = t.lower().replace("-", " ")
    mapping = {
        "pre medical": "Pre-Medical",
        "premedical": "Pre-Medical",
        "pre engineering": "Pre-Engineering",
        "preengineering": "Pre-Engineering",
        "fsc": "FSc",
        "fa": "FA",
        "ics": "ICS",
        "bscs": "BSCS",
        "bcss": "BCSS",
        "bsc": "BSC",
        "bs cs": "BSCS",
        "computer science": "Computer Science",
        "software engineering": "Software Engineering",
        "a levels": "A-Levels",
        "a level": "A-Levels",
        "alevels": "A-Levels",
        "igcse": "IGCSE",
        "gcse": "GCSE",
        "cbse": "CBSE",
        "international baccalaureate": "IB",
        "ib dp": "IB DP",
        "ib diploma": "IB Diploma",
    }
    return mapping.get(low, t)
