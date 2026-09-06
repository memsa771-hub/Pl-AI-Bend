from pai.domains.student.vault.catalog import CATALOG_VERSION, GUIDANCE_SCOPES, get_catalog_field
from pai.domains.student.vault.service import grow_vault_schema


def test_vault_catalog_covers_guidance_core():
    assert GUIDANCE_SCOPES == ("universal", "education", "application", "career")
    assert get_catalog_field("application.test_scores") is not None
    assert get_catalog_field("identity.phone").priority == "C"
    assert get_catalog_field("application.study_country").priority == "C"


def test_grow_vault_schema_is_idempotent():
    class FakeVault:
        catalog_version = "0.0.1"
        applicable_scopes = ["universal"]

    vault = FakeVault()
    assert grow_vault_schema(vault) is True
    assert vault.catalog_version == CATALOG_VERSION
    assert list(vault.applicable_scopes) == list(GUIDANCE_SCOPES)
    assert grow_vault_schema(vault) is False
