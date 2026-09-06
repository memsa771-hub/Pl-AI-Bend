"""Enumerate actual missing record attributes, never invented education ladders."""
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class DepthGap:
    key: str
    section: str
    label: str
    reason: str
    impact: float = 0.0

def classify_rung(text: str | None) -> str | None:
    """Legacy API: an original qualification name alone cannot verify its level."""
    return None

def compute_depth_gaps(*, highest_level=None, educations: list[dict[str, Any]] | None=None,
                       work_experiences=None, max_gaps=8) -> list[DepthGap]:
    out = []
    for row in educations or []:
        record_id = row.get("id")
        if not record_id:
            continue
        for attribute in ("institution", "degree", "startDate", "endDate"):
            if row.get(attribute) in (None, ""):
                out.append(DepthGap(f"education.record.{record_id}.{attribute}", "education",
                    str(row.get("degree") or "qualification"),
                    f"{attribute} is unknown for qualification {record_id}; ask only if relevant."))
    return out[:max_gaps]
