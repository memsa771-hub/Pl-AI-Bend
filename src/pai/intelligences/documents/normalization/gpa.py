"""Compatibility API for native grades; no inferred scales or conversions."""
from pai.domains.student.normalization.grades import parse_grade

parse_gpa = parse_grade

def gpa_on_4(parsed: dict) -> float | None:
    """Only an explicitly four-point value can be returned as four-point GPA."""
    grade = parse_grade(parsed)
    return grade["value"] if grade and grade["scale"] == 4 else None
