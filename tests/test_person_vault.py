from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def _auth_headers(client, email: str, password: str) -> dict[str, str]:
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["data"]["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def test_bootstrap_creates_person_and_vault(verified_user):
    client, headers, _email = verified_user
    response = client.post("/api/v1/person/bootstrap", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["person"]["email"] == "vault-user@example.com"
    assert data["vault"]["id"]
    assert "universal" in data["vault"]["applicableScopes"]


def test_bootstrap_idempotent(verified_user):
    client, headers, _ = verified_user
    first = client.post("/api/v1/person/bootstrap", headers=headers).json()["data"]
    second = client.post("/api/v1/person/bootstrap", headers=headers).json()["data"]
    assert first["person"]["id"] == second["person"]["id"]
    assert first["vault"]["id"] == second["vault"]["id"]


def test_bootstrap_rejects_unverified(vault_client, fake_provider):
    email = "unverified@example.com"
    fake_provider.users[email] = {
        "id": "unverified-1",
        "email": email,
        "password": "Password123!",
        "verified": False,
    }
    token_resp = vault_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert token_resp.status_code == 403


def test_person_profile_patch(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    me = client.get("/api/v1/person/me", headers=headers).json()["data"]
    patch = client.patch(
        "/api/v1/person/me",
        headers=headers,
        json={"fullName": "Test User", "version": me["version"]},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["fullName"] == "Test User"


def test_education_crud(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    created = client.post(
        "/api/v1/person/educations",
        headers=headers,
        json={"institution": "Test University", "degree": "BS", "graduationYear": 2024},
    )
    assert created.status_code == 201
    item_id = created.json()["data"]["item"]["id"]
    listing = client.get("/api/v1/person/educations", headers=headers)
    assert len(listing.json()["data"]["items"]) == 1
    patched = client.patch(
        f"/api/v1/person/educations/{item_id}",
        headers=headers,
        json={"major": "Computer Science"},
    )
    assert patched.status_code == 200
    deleted = client.delete(f"/api/v1/person/educations/{item_id}", headers=headers)
    assert deleted.status_code == 200


def test_sparse_vault_field(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    vault = client.get("/api/v1/vault", headers=headers).json()["data"]
    version = vault["version"]
    patch = client.patch(
        "/api/v1/vault/fields/preferences.preferred_language",
        headers=headers,
        json={"value": "en", "version": version},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["value"] == "en"


def test_unknown_field_rejected(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    response = client.patch(
        "/api/v1/vault/fields/not.a.real.field",
        headers=headers,
        json={"value": "x"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_FIELD"


def test_derived_field_rejected(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    response = client.patch(
        "/api/v1/vault/fields/auth.user_id",
        headers=headers,
        json={"value": "hacked"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "FIELD_NOT_EDITABLE"


def test_completion_scopes_after_education(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    client.post(
        "/api/v1/person/educations",
        headers=headers,
        json={"institution": "Scope U"},
    )
    vault = client.get("/api/v1/vault", headers=headers).json()["data"]
    assert "education" in vault["applicableScopes"]


def test_cross_user_access_blocked(vault_client, fake_provider):
    for email, uid in (
        ("user-a@example.com", "user-a"),
        ("user-b@example.com", "user-b"),
    ):
        fake_provider.users[email] = {
            "id": uid,
            "email": email,
            "password": "Password123!",
            "verified": True,
        }
    headers_a = _auth_headers(vault_client, "user-a@example.com", "Password123!")
    headers_b = _auth_headers(vault_client, "user-b@example.com", "Password123!")
    vault_client.post("/api/v1/person/bootstrap", headers=headers_a)
    created = vault_client.post(
        "/api/v1/person/educations",
        headers=headers_a,
        json={"institution": "Private U"},
    ).json()["data"]["item"]["id"]
    denied = vault_client.patch(
        f"/api/v1/person/educations/{created}",
        headers=headers_b,
        json={"institution": "Hacked"},
    )
    assert denied.status_code == 404


def test_concurrent_bootstrap(verified_user):
    client, headers, _ = verified_user

    def _call():
        return client.post("/api/v1/person/bootstrap", headers=headers)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _call(), range(8)))
    ids = {r.json()["data"]["person"]["id"] for r in results}
    vault_ids = {r.json()["data"]["vault"]["id"] for r in results}
    assert len(ids) == 1
    assert len(vault_ids) == 1


def test_field_history(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    vault = client.get("/api/v1/vault", headers=headers).json()["data"]
    client.patch(
        "/api/v1/vault/fields/preferences.preferred_language",
        headers=headers,
        json={"value": "fr", "version": vault["version"]},
    )
    history = client.get(
        "/api/v1/vault/fields/preferences.preferred_language/history",
        headers=headers,
    ).json()["data"]["history"]
    assert len(history) >= 1


def test_catalog_endpoint(verified_user):
    client, headers, _ = verified_user
    catalog = client.get("/api/v1/vault/catalog", headers=headers).json()["data"]
    assert catalog["catalogVersion"]
    assert any(f["key"] == "auth.email" for f in catalog["fields"])
    assert any(f["key"] == "application.test_scores" for f in catalog["fields"])


def test_vault_overview_filled_empty_required(verified_user):
    client, headers, _ = verified_user
    client.post("/api/v1/person/bootstrap", headers=headers)
    data = client.get("/api/v1/vault", headers=headers).json()["data"]
    assert "education" in data["applicableScopes"]
    assert "application" in data["applicableScopes"]
    filled_keys = {item["key"] for item in data["filled"]}
    required_keys = {item["key"] for item in data["required"]}
    empty_keys = {item["key"] for item in data["empty"]}
    assert "auth.email" in filled_keys
    assert "application.study_country" in required_keys
    assert "application.test_scores" in empty_keys
    assert data["requiredCount"] == len(data["required"])
    assert data["memory"]["engine"] == "agentspan"
