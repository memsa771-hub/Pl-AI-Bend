"""Only missing attributes of actual qualification records are enumerated."""
import pytest
from pai.intelligences.counselor.profile_depth import classify_rung, compute_depth_gaps

@pytest.mark.parametrize("name", ["PhD", "FSc", "Baccalauréat", "Abitur", "高等学校"])
def test_qualification_name_does_not_imply_a_universal_ladder(name):
    assert classify_rung(name) is None
    gaps = compute_depth_gaps(highest_level=name, educations=[])
    assert gaps == []

def test_missing_attributes_are_tied_to_owned_record_id():
    gaps = compute_depth_gaps(educations=[{"id": "q1", "degree": "Abitur", "institution": "School"}])
    assert {g.key for g in gaps} == {"education.record.q1.startDate", "education.record.q1.endDate"}

def test_complete_record_has_no_invented_lower_qualifications():
    assert compute_depth_gaps(highest_level="PhD", educations=[{
        "id": "q1", "degree": "PhD", "institution": "University",
        "startDate": "2020", "endDate": "2024"}]) == []

def test_missing_entity_id_cannot_create_an_actionable_gap():
    assert compute_depth_gaps(educations=[{"degree": "MA"}]) == []
