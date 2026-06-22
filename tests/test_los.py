"""
Tests for the LOS aggregate + intake API (#2).

Covers: full-schema round-trip (repository), POST→GET via the API, 404,
malformed-payload validation (4xx), and preservation of the post-§16 fields
(decision.source, two-layer consent, kyc.field_confidence + risk_flags).
"""
import pytest
from fastapi.testclient import TestClient

from lending.los import (
    Applicant,
    Application,
    ApplicationRepository,
    ApplicationStatus,
    Consent,
    ConsentArtifact,
    ConsentAuthorization,
    Decision,
    FieldConfidence,
    Kyc,
    create_app,
    make_engine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo() -> ApplicationRepository:
    return ApplicationRepository(make_engine())


@pytest.fixture
def client(repo) -> TestClient:
    # The API now requires auth (#38); authenticate the test client as an applicant.
    from lending.auth import AuthService

    auth = AuthService(repo._engine, "test-secret")
    _user, token = auth.register("tester@example.com", "pw", "Tester", "applicant")
    return TestClient(
        create_app(repository=repo, auth_service=auth),
        headers={"Authorization": f"Bearer {token}"},
    )


def fully_populated() -> Application:
    return Application(
        applicant=Applicant(full_name="Priya Sharma", pan="ABCDE1234F", mobile="9876543210"),
        features={"cibil_score": 720, "monthly_income": 50000},
        consent=Consent(
            authorizations=[ConsentAuthorization(purpose="bureau_pull")],
            artifacts=[ConsentArtifact(pull_purpose="bureau_pull", reference="art-1")],
        ),
        kyc=Kyc(
            status="verified",
            field_confidence=[FieldConfidence(field_name="pan", confidence=0.92, risk_flags=[])],
            risk_flags=["NONE"],
        ),
        decision=Decision(
            disposition="approve",
            source="underwriter:u123",
            reason_codes=["CLEAN"],
            rules_version="v1",
            scorecard_version="v1",
            score=75,
            band="B",
        ),
    )


# ---------------------------------------------------------------------------
# Repository round-trip — full schema preserved
# ---------------------------------------------------------------------------

def test_repository_roundtrip_preserves_all_fields(repo):
    app = fully_populated()
    repo.save(app)
    loaded = repo.get(app.application_id)
    assert loaded is not None
    # Compare the serialized forms for an exhaustive field check
    assert loaded.model_dump(mode="json") == app.model_dump(mode="json")


def test_roundtrip_preserves_post_s16_fields(repo):
    app = fully_populated()
    repo.save(app)
    loaded = repo.get(app.application_id)
    # decision.source (§16.10)
    assert loaded.decision.source == "underwriter:u123"
    # two-layer consent (§16.6)
    assert loaded.consent.authorizations[0].purpose == "bureau_pull"
    assert loaded.consent.artifacts[0].reference == "art-1"
    # kyc field_confidence + risk_flags (§16.4)
    assert loaded.kyc.field_confidence[0].confidence == 0.92
    assert loaded.kyc.risk_flags == ["NONE"]


def test_get_missing_returns_none(repo):
    assert repo.get("does-not-exist") is None


def test_save_is_upsert(repo):
    app = fully_populated()
    repo.save(app)
    app.status = ApplicationStatus.DECIDED
    repo.save(app)
    loaded = repo.get(app.application_id)
    assert loaded.status == ApplicationStatus.DECIDED


# ---------------------------------------------------------------------------
# API: POST creates, GET returns
# ---------------------------------------------------------------------------

def test_post_creates_and_get_returns(client):
    body = {
        "applicant": {"full_name": "Priya Sharma", "pan": "ABCDE1234F"},
        "features": {"cibil_score": 720},
        "consent": {"authorizations": [{"purpose": "bureau_pull"}]},
    }
    post = client.post("/applications", json=body)
    assert post.status_code == 201, post.text
    created = post.json()
    app_id = created["application_id"]
    assert created["status"] == "created"
    assert created["applicant"]["full_name"] == "Priya Sharma"

    got = client.get(f"/applications/{app_id}")
    assert got.status_code == 200
    assert got.json()["application_id"] == app_id
    assert got.json()["consent"]["authorizations"][0]["purpose"] == "bureau_pull"


def test_get_unknown_returns_404(client):
    resp = client.get("/applications/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Validation — malformed payload → 4xx
# ---------------------------------------------------------------------------

def test_malformed_payload_returns_422(client):
    # Missing required applicant.full_name
    resp = client.post("/applications", json={"applicant": {"pan": "ABCDE1234F"}})
    assert resp.status_code == 422


def test_missing_applicant_returns_422(client):
    resp = client.post("/applications", json={"features": {}})
    assert resp.status_code == 422


def test_server_assigns_id_and_timestamps(client):
    body = {"applicant": {"full_name": "Test User"}}
    created = client.post("/applications", json=body).json()
    assert created["application_id"]
    assert created["created_at"]
    assert created["updated_at"]
