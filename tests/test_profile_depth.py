"""Profile depth: 'have-it-but-shallow' gaps the binary completion probe misses."""

from __future__ import annotations

from pai.intelligences.counselor.profile_depth import (
    classify_rung,
    compute_depth_gaps,
)


def _keys(gaps) -> set[str]:
    return {g.key for g in gaps}


def test_classify_rung_maps_common_credentials():
    assert classify_rung("PhD in Physics") == "phd"
    assert classify_rung("MS Computer Science") == "master"
    assert classify_rung("BSCS") == "bachelor"
    assert classify_rung("FSc Pre-Engineering") == "higher_secondary"
    assert classify_rung("A-Levels") == "higher_secondary"
    assert classify_rung("Matriculation") == "secondary"
    assert classify_rung("O-Levels") == "secondary"
    assert classify_rung("random hobby course") is None


def test_phd_with_only_phd_row_flags_all_lower_rungs():
    gaps = compute_depth_gaps(
        highest_level="phd",
        educations=[{"degree": "PhD in CS", "institution": "TUM", "graduationYear": 2024, "gpa": 3.9}],
    )
    keys = _keys(gaps)
    assert "education.level.master" in keys
    assert "education.level.bachelor" in keys
    assert "education.level.higher_secondary" in keys
    assert "education.level.secondary" in keys
    # The rung they already have is never a gap.
    assert "education.level.phd" not in keys


def test_master_with_master_and_bachelor_only_flags_pre_university():
    gaps = compute_depth_gaps(
        highest_level="master",
        educations=[
            {"degree": "MS Data Science", "institution": "X", "graduationYear": 2022, "gpa": 3.7},
            {"degree": "BSc Software Engineering", "institution": "Y", "graduationYear": 2020, "gpa": 3.5},
        ],
    )
    keys = _keys(gaps)
    assert "education.level.higher_secondary" in keys
    assert "education.level.secondary" in keys
    assert "education.level.master" not in keys
    assert "education.level.bachelor" not in keys


def test_full_ladder_present_has_no_level_gaps():
    gaps = compute_depth_gaps(
        highest_level="bachelor",
        educations=[
            {"degree": "BSCS", "institution": "Bahria", "graduationYear": 2021, "gpa": 3.4, "major": "CS"},
            {"degree": "A-Levels", "institution": "LGS", "graduationYear": 2017, "gpa": 3.8, "major": "Sciences"},
            {"degree": "Matric", "institution": "LGS", "graduationYear": 2015, "gpa": 3.9, "major": "Science"},
        ],
    )
    assert not any(g.key.startswith("education.level.") for g in gaps)


def test_missing_per_degree_detail_is_flagged():
    gaps = compute_depth_gaps(
        highest_level="bachelor",
        educations=[
            {"degree": "BSCS", "major": "CS", "gpa": 3.4, "graduationYear": 2021},  # no institution
            {"degree": "A-Levels", "institution": "LGS", "graduationYear": 2017, "gpa": 3.8, "major": "Sci"},
            {"degree": "Matric", "institution": "LGS", "graduationYear": 2015, "gpa": 3.9, "major": "Sci"},
        ],
    )
    assert "education.detail.bachelor.institution" in _keys(gaps)


def test_career_row_without_description_is_flagged():
    gaps = compute_depth_gaps(
        work_experiences=[{"organization": "Acme", "title": "Engineer"}],
    )
    assert any(g.section == "career" and g.key == "career.detail.description" for g in gaps)


def test_career_row_with_description_is_not_flagged():
    gaps = compute_depth_gaps(
        work_experiences=[
            {"organization": "Acme", "title": "Engineer", "description": "Built the billing service."}
        ],
    )
    assert not any(g.section == "career" for g in gaps)


def test_empty_profile_yields_no_depth_gaps():
    assert compute_depth_gaps() == []


def test_gaps_are_capped():
    gaps = compute_depth_gaps(
        highest_level="phd",
        educations=[{"degree": "PhD"}],  # ladder + detail will stack
        work_experiences=[{"organization": f"Org{i}"} for i in range(20)],
        max_gaps=5,
    )
    assert len(gaps) <= 5
