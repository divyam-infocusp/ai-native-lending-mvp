"""
Tests for the Origination control API (#36).

Each endpoint is exercised against a mock backend — a scripted Onboarding Copilot
(no Gemini) and an injected workflow starter (no Temporal) — so the HTTP surface
is verified end-to-end without external services.
"""
from langgraph.checkpoint.memory import MemorySaver
from fastapi.testclient import TestClient

from lending.agents import OnboardingCopilot
from lending.audit import AuditStore
from lending.auth import AuthService
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.los.api import create_app
from lending.los.schema import Decision, Disposition


def _scripted(outputs):
    it = iter(outputs)
    return lambda context, tool_result: next(it)


def _turn(msg, extracted=None):
    return {"extracted": extracted or {}, "assistant_message": msg, "reasoning": ""}


def _make(copilot=None):
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    auth = AuthService(engine, "test-secret")
    applicant, token = auth.register("priya@example.com", "pw", "Priya Sharma", "applicant")
    started: list[str] = []

    def starter(application_id):           # injected — no Temporal
        started.append(application_id)
        return f"run-{application_id}"

    cop = copilot or OnboardingCopilot(
        reason=_scripted([_turn("Hi! What's your PAN and date of birth?")]),
        checkpointer=MemorySaver(),
    )
    app = create_app(repo, audit=audit, copilot=cop, workflow_starter=starter, auth_service=auth)
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    return client, repo, audit, started, applicant


def _create(client, full_name="Priya Sharma") -> str:
    resp = client.post("/applications", json={"applicant": {"full_name": full_name}})
    assert resp.status_code == 201
    return resp.json()["application_id"]


# ---------------------------------------------------------------------------
# Onboarding conversation
# ---------------------------------------------------------------------------

def test_onboarding_message_runs_a_copilot_turn():
    client, *_ = _make()
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/onboarding/message", json={"message": None})
    assert resp.status_code == 200
    body = resp.json()
    assert body["assistant_message"].startswith("Hi!")
    assert body["complete"] is False                 # nothing collected yet
    assert "collected" in body and "missing" in body


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

def test_submit_details_applies_fields_and_reports_completeness():
    client, repo, *_ = _make()
    app_id = _create(client)
    # Form-fill path (#42): set the data fields directly.
    fields = {
        "full_name": "Ravi Kumar", "date_of_birth": "1990-05-10", "pan": "ABCDE1234F",
        "aadhaar": "234567890124", "mobile": "9876543210", "current_address": "1 MG Rd, Pune",
        "employment_type": "salaried", "employer_name": "Infosys",
        "employment_tenure_months": "48", "monthly_income": "85000",
        "loan_amount_requested": "300000", "loan_tenure_months": "36", "loan_purpose": "renovation",
    }
    resp = client.post(f"/applications/{app_id}/details", json={"fields": fields})
    assert resp.status_code == 200
    body = resp.json()
    # all data fields present → only documents remain
    assert body["complete"] is False
    assert all(m.startswith("document:") for m in body["missing"])

    app = repo.get(app_id)
    assert app.applicant.full_name == "Ravi Kumar"
    assert app.applicant.pan == "ABCDE1234F"
    assert app.features["monthly_income"] == 85000      # coerced to number
    assert app.features["employment_tenure_months"] == 48


def test_capture_consent_reflected_on_aggregate():
    client, repo, *_ = _make()
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/consent", json={"purpose": "bureau_pull"})
    assert resp.status_code == 200
    auths = repo.get(app_id).consent.authorizations
    assert [a.purpose for a in auths] == ["bureau_pull"]
    assert auths[0].status.value == "active"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def test_upload_document_registers_presence():
    client, repo, *_ = _make()
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/documents",
                       json={"doc_type": "salary_slips", "reference": "s3://x/slip.pdf"})
    assert resp.status_code == 201
    docs = repo.get(app_id).features["documents"]
    assert docs["salary_slips"]["uploaded"] is True
    assert docs["salary_slips"]["verified"] is None      # left for KYC (#19)


def test_upload_unknown_document_type_is_400():
    client, *_ = _make()
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/documents", json={"doc_type": "selfie"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Start workflow
# ---------------------------------------------------------------------------

def test_start_invokes_the_injected_starter():
    client, _, _, started, _ = _make()
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/start")
    assert resp.status_code == 202
    assert resp.json()["workflow_run"] == f"run-{app_id}"
    assert started == [app_id]


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

def test_audit_trail_returned_in_order():
    client, *_ = _make()
    app_id = _create(client)
    client.post(f"/applications/{app_id}/consent", json={"purpose": "bureau_pull"})
    resp = client.get(f"/applications/{app_id}/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert any(e["event_type"] == "consent" for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)


# ---------------------------------------------------------------------------
# Read surfaces decision + offer
# ---------------------------------------------------------------------------

def test_read_application_surfaces_decision_and_offer():
    client, repo, _, _, applicant = _make()
    app = Application(applicant=Applicant(full_name="Priya Sharma"), owner_user_id=applicant.user_id)
    app.decision = Decision(disposition=Disposition.APPROVE, band="A", score=110)
    app.features = {"offer_letter": {"sanctioned_amount": 300000, "emi": 9757}}
    repo.save(app)

    body = client.get(f"/applications/{app.application_id}").json()
    assert body["decision"]["disposition"] == "approve"
    assert body["decision"]["band"] == "A"
    assert body["features"]["offer_letter"]["sanctioned_amount"] == 300000


# ---------------------------------------------------------------------------
# 404s
# ---------------------------------------------------------------------------

def test_list_applications_returns_summaries():
    client, *_ = _make()
    a1 = _create(client, "Priya Sharma")
    a2 = _create(client, "Rahul Verma")
    body = client.get("/applications").json()
    ids = {item["application_id"] for item in body["applications"]}
    assert {a1, a2} <= ids
    sample = next(i for i in body["applications"] if i["application_id"] == a1)
    assert sample["applicant_name"] == "Priya Sharma"
    assert "workflow_state" in sample and "disposition" in sample


def test_unknown_application_is_404():
    client, *_ = _make()
    assert client.get("/applications/nope/audit").status_code == 404
    assert client.post("/applications/nope/consent", json={"purpose": "x"}).status_code == 404
    assert client.post("/applications/nope/start").status_code == 404
