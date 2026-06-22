"""
Tests for the Decision QA Agent + offer delivery (#23).

QA invariants on the decision-of-record (well-formed, version-stamped, non-approve
carries adverse-action reasons); offer-letter assembly with all real-world terms;
borderline → REFERRED (via the decision engine, before delivery); and delivery
sending a notification + routing to e-sign.
"""
from datetime import datetime, timezone

from lending.adapters import make_mock_esign_harness, make_mock_notifications_harness
from lending.agents import (
    assemble_offer_letter,
    deliver_offer,
    qa_check_decision,
)
from lending.audit import AuditStore
from lending.decision import decide, record_decision
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.los.schema import Decision, Disposition
from lending.pricing import Offer
from lending.rules_engine import ApplicantFeatures

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)

# A clean, approvable applicant (already-assembled engine inputs, as #20 leaves them).
CLEAN = {
    "age": 32, "monthly_income": 90_000, "monthly_obligations": 3_000, "cibil_score": 780,
    "employment_tenure_months": 60, "loan_amount_requested": 300_000, "loan_tenure_months": 36,
    "is_salaried": True, "has_cibil_record": True,
}


def _stores():
    engine = make_engine()
    return ApplicationRepository(engine), AuditStore(engine)


def _seed_decided(repo, audit, features):
    app = Application(applicant=Applicant(full_name="Priya Sharma"), features=dict(features))
    repo.save(app)
    decision = decide(ApplicantFeatures(**features))
    record_decision(repo, audit, app.application_id, decision)
    return app.application_id, decision


def _harnesses():
    n, _ = make_mock_notifications_harness()
    e, _ = make_mock_esign_harness()
    return n, e


# ---------------------------------------------------------------------------
# QA invariants
# ---------------------------------------------------------------------------

def test_qa_passes_for_well_formed_approve():
    decision = decide(ApplicantFeatures(**CLEAN))
    assert decision.disposition == Disposition.APPROVE
    assert qa_check_decision(decision).ok is True


def test_qa_requires_adverse_action_reasons_for_decline():
    # A decline that (wrongly) carries no reasons / explanation must fail QA.
    bad = Decision(disposition=Disposition.DECLINE, reason_codes=[], explanation="")
    result = qa_check_decision(bad)
    assert result.ok is False
    assert any("reason codes" in i for i in result.issues)
    assert any("adverse-action" in i for i in result.issues)


def test_qa_flags_missing_version_set():
    bad = Decision(disposition=Disposition.APPROVE, version_set=None)
    assert qa_check_decision(bad).ok is False


def test_real_decline_carries_adverse_action_and_passes_qa():
    # The engine (#18) renders adverse-action text for a knockout → QA passes.
    knockout = {**CLEAN, "cibil_score": 600}
    decision = decide(ApplicantFeatures(**knockout))
    assert decision.disposition == Disposition.DECLINE
    assert decision.reason_codes and decision.explanation       # 100% carry reasons
    assert qa_check_decision(decision).ok is True


# ---------------------------------------------------------------------------
# Borderline → REFERRED (decided before delivery)
# ---------------------------------------------------------------------------

def test_borderline_is_referred_not_offered():
    # High obligations → DTI soft hit → REFER (the engine routes borderline away).
    borderline = {**CLEAN, "monthly_obligations": 60_000}
    decision = decide(ApplicantFeatures(**borderline))
    assert decision.disposition == Disposition.REFER


# ---------------------------------------------------------------------------
# Offer-letter assembly
# ---------------------------------------------------------------------------

def test_offer_letter_has_all_terms():
    offer = Offer(rate=14.5, amount=300_000.0, tenure=36, emi=10_323.0)
    letter = assemble_offer_letter(offer, now=NOW)
    # core terms
    assert letter["sanctioned_amount"] == 300_000.0
    assert letter["interest_rate"] == 14.5
    assert letter["tenure_months"] == 36
    assert letter["emi"] == 10_323.0
    # real-world wrapper
    assert letter["processing_fee"] == round(300_000 * 0.02, 2)
    assert letter["gst_on_fee"] == round(letter["processing_fee"] * 0.18, 2)
    assert letter["total_amount_payable"] == round(10_323.0 * 36, 2)
    assert letter["total_interest_payable"] == round(10_323.0 * 36 - 300_000, 2)
    assert letter["net_disbursal_amount"] == round(300_000 - letter["processing_fee"] - letter["gst_on_fee"], 2)
    assert letter["valid_until"].startswith("2026-07-01")   # NOW + 30 days
    assert letter["terms"]


# ---------------------------------------------------------------------------
# Delivery — QA, price, persist, notify, e-sign
# ---------------------------------------------------------------------------

def test_deliver_offer_prices_notifies_and_esigns():
    repo, audit = _stores()
    app_id, _ = _seed_decided(repo, audit, CLEAN)
    notify, esign = _harnesses()

    result = deliver_offer(repo, audit, app_id, notify_harness=notify, esign_harness=esign, now=NOW)
    assert result.status == "delivered"
    # offer-letter terms present
    assert result.offer_letter["emi"] > 0
    assert result.offer_letter["sanctioned_amount"] > 0

    app = repo.get(app_id)
    assert app.features["offer_letter"]["interest_rate"] in (10.5, 14.5, 18.0, 22.0)

    # notification dispatched + e-sign requested (recorded in audit)
    deliver_events = [e for e in audit.reconstruct(app_id)
                      if e.event_type == "agent_reasoning" and e.payload.get("action") == "offer_delivered"]
    assert len(deliver_events) == 1
    assert deliver_events[0].payload["esign_envelope"]


def test_deliver_offer_blocks_a_non_approval():
    repo, audit = _stores()
    knockout = {**CLEAN, "cibil_score": 600}     # engine declines
    app_id, decision = _seed_decided(repo, audit, knockout)
    assert decision.disposition == Disposition.DECLINE
    notify, esign = _harnesses()

    result = deliver_offer(repo, audit, app_id, notify_harness=notify, esign_harness=esign, now=NOW)
    assert result.status == "blocked"
    assert repo.get(app_id).features.get("offer_letter") is None
