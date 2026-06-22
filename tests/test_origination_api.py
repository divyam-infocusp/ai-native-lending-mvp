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
    started: list[str] = []

    def starter(application_id):           # injected — no Temporal
        started.append(application_id)
        return f"run-{application_id}"

    cop = copilot or OnboardingCopilot(
        reason=_scripted([_turn("Hi! What's your PAN and date of birth?")]),
        checkpointer=MemorySaver(),
    )
    app = create_app(repo, audit=audit, copilot=cop, workflow_starter=starter)
    return TestClient(app), repo, audit, started


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
    client, _, _, started = _make()
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
    client, repo, *_ = _make()
    app = Application(applicant=Applicant(full_name="Priya Sharma"))
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

def test_unknown_application_is_404():
    client, *_ = _make()
    assert client.get("/applications/nope/audit").status_code == 404
    assert client.post("/applications/nope/consent", json={"purpose": "x"}).status_code == 404
    assert client.post("/applications/nope/start").status_code == 404
