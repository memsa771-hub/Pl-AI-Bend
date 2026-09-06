from __future__ import annotations

import pytest

from pai.domains.student.normalization.geo import coerce_country, country_codes_from_value, extract_countries_from_text
from pai.domains.student.normalization.phone import normalize_phone


def test_coerce_country_accepts_iso_and_exonyms():
    assert coerce_country("PK") == "PK"
    assert coerce_country("Pakistan") == "PK"
    assert coerce_country("UK") == "GB"
    assert coerce_country("Turkey") == "TR"
    assert coerce_country("UAE") == "AE"
    assert coerce_country("Russia") == "RU"


def test_extract_countries_uses_iso_alpha2_not_display_names():
    assert extract_countries_from_text("MS AI in Germany and China") == ["DE", "CN"]
    assert extract_countries_from_text("study in the UK or UAE") == ["GB", "AE"]
    assert "US" not in extract_countries_from_text("tell us about your plans")


def test_country_codes_from_value_keeps_compound_names():
    assert country_codes_from_value("Trinidad and Tobago") == ["TT"]
    assert country_codes_from_value("Germany, China") == ["DE", "CN"]


def test_normalize_phone_e164():
    assert normalize_phone("+92 300 1234567") == "+923001234567"
    assert normalize_phone("03001234567", default_region="PK") == "+923001234567"
    with pytest.raises(ValueError, match="country code"):
        normalize_phone("03001234567")
    with pytest.raises(ValueError, match="phone"):
        normalize_phone("not-a-number")
