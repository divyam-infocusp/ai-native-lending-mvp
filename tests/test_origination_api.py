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


def _lead(segment_fit, reason_code):
    """A scripted Lead Qualification output for the early intent gate (#21)."""
    return {
        "segment_fit": segment_fit,
        "employment_type": "salaried" if segment_fit == "in_segment" else "unknown",
        "reason_code": reason_code,
        "confidence": 0.9 if segment_fit == "uncertain" else 0.95,
        "reasoning": "test",
    }


def _make(copilot=None, lead_reason=None):
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
    app = create_app(repo, audit=audit, copilot=cop, lead_reason=lead_reason,
                     workflow_starter=starter, auth_service=auth)
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    return client, repo, audit, started, applicant


def _create(client, full_name="Priya Sharma") -> str:
    resp = client.post("/applications", json={"applicant": {"full_name": full_name}})
    assert resp.status_code == 201
    return resp.json()["application_id"]


def _complete(client, app_id) -> None:
    """Fill all required data fields + upload every required document, so the
    application is complete enough to enter the workflow (#start gate)."""
    client.post(f"/applications/{app_id}/details", json={"fields": {
        "full_name": "Ravi Kumar", "date_of_birth": "1990-05-10", "pan": "ABCDE1234F",
        "aadhaar": "234567890124", "mobile": "9876543210", "current_address": "1 MG Rd, Pune",
        "employment_type": "salaried", "employer_name": "Infosys",
        "employment_tenure_months": "48", "monthly_income": "85000",
        "loan_amount_requested": "300000", "loan_tenure_months": "36", "loan_purpose": "renovation",
    }})
    for d in ("aadhaar_card", "pan_card", "salary_slips", "form16"):
        client.post(f"/applications/{app_id}/documents", json={"doc_type": d})


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
# Early lead-intent gate (#21) — runs on the first substantive chat turn
# ---------------------------------------------------------------------------

def test_onboarding_blocks_out_of_segment_lead():
    client, repo, *_ = _make(
        lead_reason=_scripted([_lead("out_of_segment", "OUT_OF_SCOPE_NOT_A_LOAN")]))
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/onboarding/message",
                       json={"message": "I want to sell my old car, can you help?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "blocked"
    assert body["complete"] is False
    assert body["collected"] == {}                    # no PII collected
    # verdict persisted so the gate doesn't re-run
    app = repo.get(app_id)
    assert (app.features or {}).get("lead_intent", {}).get("status") == "blocked"


def test_onboarding_in_segment_lead_proceeds_to_copilot():
    cop = OnboardingCopilot(
        reason=_scripted([_turn("Hi! What's your PAN and date of birth?")]),
        checkpointer=MemorySaver(),
    )
    client, *_ = _make(copilot=cop, lead_reason=_scripted([_lead("in_segment", "PROCEED")]))
    app_id = _create(client)
    resp = client.post(f"/applications/{app_id}/onboarding/message",
                       json={"message": "I need a personal loan of 3 lakh for home renovation"})
    body = resp.json()
    assert body["intent"] == "ok"
    assert body["assistant_message"].startswith("Hi!")   # copilot turn ran


def test_onboarding_uncertain_lead_asks_clarification_then_proceeds():
    cop = OnboardingCopilot(
        reason=_scripted([_turn("Hi! What's your PAN and date of birth?")]),
        checkpointer=MemorySaver(),
    )
    client, *_ = _make(copilot=cop, lead_reason=_scripted([
        _lead("uncertain", "INSUFFICIENT_INFO"), _lead("uncertain", "INSUFFICIENT_INFO")]))
    app_id = _create(client)
    r1 = client.post(f"/applications/{app_id}/onboarding/message",
                     json={"message": "hello"}).json()
    assert r1["intent"] == "needs_clarification"          # one clarifying question
    r2 = client.post(f"/applications/{app_id}/onboarding/message",
                     json={"message": "I think I might want some money"}).json()
    assert r2["intent"] == "ok"                           # never traps the applicant


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


def test_delete_document_clears_presence_for_reattach():
    client, repo, *_ = _make()
    app_id = _create(client)
    client.post(f"/applications/{app_id}/documents", json={"doc_type": "pan_card"})
    assert repo.get(app_id).features["documents"]["pan_card"]["uploaded"] is True

    resp = client.delete(f"/applications/{app_id}/documents/pan_card")
    assert resp.status_code == 200
    assert resp.json()["uploaded"] is False
    # presence cleared → the slot is empty again and re-attach is possible
    assert "pan_card" not in (repo.get(app_id).features.get("documents") or {})

    # idempotent: deleting an already-removed doc still succeeds
    assert client.delete(f"/applications/{app_id}/documents/pan_card").status_code == 200


def test_upload_document_file_stores_bytes_and_registers(tmp_path):
    # real file upload (#9, Phase A): bytes land in the store + presence is recorded
    from lending.storage import LocalDocumentStore

    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    auth = AuthService(engine, "test-secret")
    _appl, token = auth.register("a@example.com", "pw", "A", "applicant")
    store = LocalDocumentStore(str(tmp_path))
    app = create_app(repo, audit=audit, auth_service=auth,
                     workflow_starter=lambda i: i, document_store=store)
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    app_id = client.post("/applications", json={"applicant": {"full_name": "A"}}).json()["application_id"]

    resp = client.post(
        f"/applications/{app_id}/documents/file",
        data={"doc_type": "salary_slips"},
        files={"file": ("slip.pdf", b"PDFBYTES", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["bytes"] == 8
    # bytes are retrievable by the worker via the same store
    assert store.get(app_id, "salary_slips").data == b"PDFBYTES"
    # presence registered, pointing at the stored reference
    doc = repo.get(app_id).features["documents"]["salary_slips"]
    assert doc["uploaded"] is True and doc["reference"].startswith("file://")


# ---------------------------------------------------------------------------
# Start workflow
# ---------------------------------------------------------------------------

def test_start_invokes_the_injected_starter():
    client, _, _, started, _ = _make()
    app_id = _create(client)
    _complete(client, app_id)                  # workflow entry requires completeness
    resp = client.post(f"/applications/{app_id}/start")
    assert resp.status_code == 202
    assert resp.json()["workflow_run"] == f"run-{app_id}"
    assert started == [app_id]


def test_start_rejects_incomplete_application():
    client, _, _, started, _ = _make()
    app_id = _create(client)                    # only a name → nowhere near complete
    resp = client.post(f"/applications/{app_id}/start")
    assert resp.status_code == 422
    assert "incomplete" in resp.json()["detail"]
    assert started == []                        # workflow never started


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


# ---------------------------------------------------------------------------
# Resolve / override (#15, 15b)
# ---------------------------------------------------------------------------

def _uw_stack():
    """A stack authed as an underwriter, with a fake resolve-signal capturing calls."""
    from lending.workflow.statemachine import State  # noqa
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    auth = AuthService(engine, "test-secret")
    _uw, utoken = auth.register("uw@example.com", "pw", "UW", "underwriter")
    _appl, atoken = auth.register("a@example.com", "pw", "A", "applicant")
    signals: list = []
    app = create_app(repo, audit=audit, auth_service=auth,
                     workflow_starter=lambda i: f"run-{i}",
                     resolve_signal=lambda i, res: signals.append((i, res)))
    client = TestClient(app, headers={"Authorization": f"Bearer {utoken}"})
    return client, repo, audit, signals, atoken


def _seed_parked(repo, state, *, reason_codes=("HIGH_DTI",)):
    from lending.los.schema import Decision, Disposition
    app = Application(applicant=Applicant(full_name="Priya Sharma"))
    app.workflow_state = state
    if state == "REFERRED":
        app.decision = Decision(disposition=Disposition.REFER, source="engine",
                                reason_codes=list(reason_codes), band="B", score=70,
                                rules_version="v1", scorecard_version="v1")
    repo.save(app)
    return app.application_id


def test_resolve_referred_to_approved_overrides_decision_and_signals():
    client, repo, _, signals, _ = _uw_stack()
    app_id = _seed_parked(repo, "REFERRED")
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE",
                          "note": "Stable salary history; DTI breach is one-off."})
    assert r.status_code == 200, r.text
    # decision-of-record overridden to the human's call, sourced to the underwriter
    dec = repo.get(app_id).decision
    assert dec.disposition.value == "approve"
    assert dec.source.startswith("underwriter:")
    # the parked workflow was signaled with the target state + justification note
    assert signals and signals[-1][1]["to_state"] == "APPROVED"
    assert signals[-1][1]["note"] == "Stable salary history; DTI breach is one-off."


def test_reject_kyc_exception_records_human_decline():
    client, repo, _, signals, _ = _uw_stack()
    app_id = _seed_parked(repo, "KYC_EXCEPTION")        # no engine decision yet
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "DECLINED", "reason_code": "DOC_NOT_GENUINE",
                          "note": "Identity document is forged."})
    assert r.status_code == 200, r.text
    dec = repo.get(app_id).decision                      # human decline became the record
    assert dec.disposition.value == "decline"
    assert dec.source.startswith("underwriter:")
    assert "DOC_NOT_GENUINE" in dec.reason_codes
    assert signals and signals[-1][1]["to_state"] == "DECLINED"


def test_policy_endpoint_returns_rules_and_bands():
    client, *_ = _make()
    body = client.get("/policy").json()
    assert body["version"] == "v1"
    codes = {r["reason_code"] for r in body["rules"]}
    assert {"LOW_CIBIL", "HIGH_DTI"} <= codes
    hard = {r["reason_code"] for r in body["rules"] if r["type"] == "hard"}
    assert "LOW_CIBIL" in hard and "HIGH_DTI" not in hard          # hard vs soft surfaced
    assert any(b["band"] == "A" and b["rate_pct"] for b in body["bands"])
    assert body["documents"]["key_fields"]


def test_create_rejects_unknown_demo_scenario():
    client, *_ = _make()
    r = client.post("/applications",
                    json={"applicant": {"full_name": "X"}, "features": {"demo_scenario": "bogus"}})
    assert r.status_code == 422


def test_resolve_reports_409_when_workflow_not_running():
    # A parked case whose workflow has already completed (e.g. predates parking):
    # signalling fails, and the endpoint must surface 409, not an unhandled 500.
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    auth = AuthService(engine, "test-secret")
    _uw, utoken = auth.register("uw2@example.com", "pw", "UW", "underwriter")

    def boom(_id, _res):
        raise RuntimeError("workflow execution already completed")

    app = create_app(repo, audit=audit, auth_service=auth,
                     workflow_starter=lambda i: f"run-{i}", resolve_signal=boom)
    client = TestClient(app, headers={"Authorization": f"Bearer {utoken}"})
    app_id = _seed_parked(repo, "REFERRED")
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE", "note": "ok"})
    assert r.status_code == 409


def test_resolve_requires_a_justification_note():
    client, repo, *_ = _uw_stack()
    app_id = _seed_parked(repo, "REFERRED")
    # no note → 422
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE"})
    assert r.status_code == 422
    # blank note → 422
    r2 = client.post(f"/applications/{app_id}/resolve",
                     json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE", "note": "   "})
    assert r2.status_code == 422


def test_resolve_requires_underwriter():
    client, repo, _, _, atoken = _uw_stack()
    app_id = _seed_parked(repo, "REFERRED")
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE"},
                    headers={"Authorization": f"Bearer {atoken}"})
    assert r.status_code == 403


def test_resolve_rejects_unknown_reason_code():
    client, repo, *_ = _uw_stack()
    app_id = _seed_parked(repo, "REFERRED")
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "BECAUSE_I_SAID_SO"})
    assert r.status_code == 422


def test_resolve_rejects_illegal_transition():
    client, repo, *_ = _uw_stack()
    app_id = _seed_parked(repo, "REFERRED")
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "KYC_VERIFIED", "reason_code": "MANUAL_APPROVE"})
    assert r.status_code == 422


def test_resolve_rejects_when_not_parked():
    client, repo, *_ = _uw_stack()
    app_id = _seed_parked(repo, "OFFER_GENERATED")
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE"})
    assert r.status_code == 409


def test_hard_knockout_is_non_overridable():
    client, repo, *_ = _uw_stack()
    # a parked case carrying a hard-knockout reason cannot be approved
    app_id = _seed_parked(repo, "REFERRED", reason_codes=("LOW_CIBIL",))
    r = client.post(f"/applications/{app_id}/resolve",
                    json={"to_state": "APPROVED", "reason_code": "MANUAL_APPROVE",
                          "note": "Compensating factors."})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Application creation is applicant-only (#38) — underwriters review, never own
# ---------------------------------------------------------------------------

def test_create_application_is_applicant_only():
    client, repo, _, _, atoken = _uw_stack()    # client is authed as the underwriter
    # an underwriter token cannot create an application (would orphan it from the
    # applicant's owner-scoped list)
    r = client.post("/applications", json={"applicant": {"full_name": "X"}})
    assert r.status_code == 403
    # an applicant token can, and owns it
    r2 = client.post("/applications", json={"applicant": {"full_name": "X"}},
                     headers={"Authorization": f"Bearer {atoken}"})
    assert r2.status_code == 201
    assert r2.json()["owner_user_id"] is not None
