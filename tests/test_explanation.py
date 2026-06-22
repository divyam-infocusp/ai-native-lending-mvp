"""
Tests for the Adverse-Action / Explanation Renderer + faithfulness check (#17).

Covers: coverage exactness, orphan + omission rejection, per-language template
selection with code-inserted numbers, missing-template guard, and the
GET /applications/{id}/explanation endpoint.
"""
import pytest
from fastapi.testclient import TestClient

from lending.explanation import (
    FaithfulnessError,
    MissingTemplateError,
    build_context,
    covered_reason_codes,
    render,
    render_faithful,
    verify_faithful,
)
from lending.los import (
    Applicant,
    Application,
    ApplicationRepository,
    Decision,
    create_app,
    make_engine,
)

CTX = {
    "cibil_score": 580, "min_cibil_score": 650,
    "monthly_income": 12000, "min_monthly_income": 20000,
    "max_dti_pct": 50,
    "loan_amount_requested": 3000000, "max_loan_amount": 2000000,
    "age": 19, "min_age": 21, "max_age": 60,
    "employment_tenure_months": 3, "min_employment_months": 6,
}


# ---------------------------------------------------------------------------
# Rendering basics
# ---------------------------------------------------------------------------

def test_render_inserts_numbers():
    r = render(["LOW_CIBIL"], "en", CTX)
    assert "580" in r.text and "650" in r.text
    assert r.reason_codes == ["LOW_CIBIL"]


def test_render_multiple_reasons():
    r = render(["LOW_CIBIL", "INSUFFICIENT_INCOME"], "en", CTX)
    assert len(r.sentences) == 2
    assert "credit bureau score" in r.text
    assert "declared monthly income" in r.text


def test_render_empty_is_empty():
    r = render([], "en", CTX)
    assert r.text == ""
    assert verify_faithful([], r.text) is True


# ---------------------------------------------------------------------------
# Faithfulness — coverage exactness (the §16.1 guard)
# ---------------------------------------------------------------------------

def test_faithful_covers_exactly():
    codes = ["LOW_CIBIL", "HIGH_DTI"]
    text = render(codes, "en", CTX).text
    assert verify_faithful(codes, text) is True
    assert covered_reason_codes(text) == set(codes)


def test_orphan_claim_fails():
    # Text covers a reason that was NOT in the fired set → not faithful
    fired = ["LOW_CIBIL"]
    text = render(["LOW_CIBIL", "HIGH_DTI"], "en", CTX).text  # extra HIGH_DTI claim
    assert verify_faithful(fired, text) is False


def test_omission_fails():
    # Text omits a fired reason → not faithful
    fired = ["LOW_CIBIL", "HIGH_DTI"]
    text = render(["LOW_CIBIL"], "en", CTX).text  # HIGH_DTI missing
    assert verify_faithful(fired, text) is False


def test_render_faithful_passes_on_match():
    r = render_faithful(["LOW_CIBIL", "SHORT_EMPLOYMENT"], "en", CTX)
    assert "credit bureau score" in r.text


def test_underage_overage_not_confused():
    # Mutually-exclusive age reasons must not cross-detect (distinct signatures)
    text = render(["UNDERAGE"], "en", CTX).text
    assert covered_reason_codes(text) == {"UNDERAGE"}
    assert verify_faithful(["OVERAGE"], text) is False


# ---------------------------------------------------------------------------
# Per-language selection with code-inserted numbers (§16.11)
# ---------------------------------------------------------------------------

def test_per_language_same_numbers_different_text():
    en = render(["LOW_CIBIL"], "en", CTX).text
    hi = render(["LOW_CIBIL"], "hi", CTX).text
    # same code-inserted numbers
    assert "580" in en and "580" in hi
    assert "650" in en and "650" in hi
    # different (template-sourced) legal text
    assert en != hi
    assert "credit bureau score" in en
    assert "क्रेडिट ब्यूरो स्कोर" in hi


def test_faithfulness_is_language_scoped():
    hi_text = render(["LOW_CIBIL"], "hi", CTX).text
    assert verify_faithful(["LOW_CIBIL"], hi_text, language="hi") is True


def test_missing_template_raises():
    # HIGH_DTI has no Hindi template → never free-translate
    with pytest.raises(MissingTemplateError):
        render(["HIGH_DTI"], "hi", CTX)


# ---------------------------------------------------------------------------
# build_context from features + policy
# ---------------------------------------------------------------------------

def test_build_context_pulls_thresholds_from_policy():
    ctx = build_context({"cibil_score": 600}, "v1")
    assert ctx["cibil_score"] == 600
    assert ctx["min_cibil_score"] == 650  # from RULES_POLICY v1


# ---------------------------------------------------------------------------
# GET /applications/{id}/explanation
# ---------------------------------------------------------------------------

@pytest.fixture
def client_and_repo():
    from lending.auth import AuthService

    repo = ApplicationRepository(make_engine())
    auth = AuthService(repo._engine, "test-secret")
    # underwriter: can read any application's explanation regardless of owner (#38)
    _user, token = auth.register("uw@example.com", "pw", "UW", "underwriter")
    client = TestClient(
        create_app(repository=repo, auth_service=auth),
        headers={"Authorization": f"Bearer {token}"},
    )
    return client, repo


def _seed_declined(repo) -> str:
    app = Application(
        applicant=Applicant(full_name="Raj Kumar"),
        features={"cibil_score": 580, "monthly_income": 12000},
        decision=Decision(disposition="decline", source="engine",
                          reason_codes=["LOW_CIBIL"], rules_version="v1"),
    )
    repo.save(app)
    return app.application_id


def test_explanation_endpoint_returns_codes_and_text(client_and_repo):
    client, repo = client_and_repo
    app_id = _seed_declined(repo)
    resp = client.get(f"/applications/{app_id}/explanation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reason_codes"] == ["LOW_CIBIL"]
    assert "580" in body["text"] and "650" in body["text"]


def test_explanation_endpoint_language_param(client_and_repo):
    client, repo = client_and_repo
    app_id = _seed_declined(repo)
    resp = client.get(f"/applications/{app_id}/explanation", params={"language": "hi"})
    assert resp.status_code == 200
    assert "क्रेडिट ब्यूरो स्कोर" in resp.json()["text"]


def test_explanation_endpoint_404(client_and_repo):
    client, _ = client_and_repo
    assert client.get("/applications/ghost/explanation").status_code == 404


def test_explanation_endpoint_no_decision_is_empty(client_and_repo):
    client, repo = client_and_repo
    app = Application(applicant=Applicant(full_name="Priya"), features={})
    repo.save(app)
    resp = client.get(f"/applications/{app.application_id}/explanation")
    assert resp.status_code == 200
    assert resp.json()["reason_codes"] == []
    assert resp.json()["text"] == ""
