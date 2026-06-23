"""
Tests for the origination state machine + Temporal workflow (#13).

Pure tests (no Temporal): legal/illegal transitions, happy-path legality.
Temporal tests (in-process time-skipping server): end-to-end to OFFER_GENERATED
with audited transitions, and a replay test proving deterministic recovery.
"""
import uuid

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from lending.adapters import (
    make_mock_bureau_harness,
    make_mock_esign_harness,
    make_mock_notifications_harness,
)
from lending.adapters.bureau import CLEAN_REPORT, HARD_INQUIRY
from lending.agents import BUREAU_PULL_PURPOSE
from lending.audit import AuditStore
from lending.consent import capture_authorization
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.workflow import (
    HAPPY_PATH,
    IllegalTransition,
    LoanOriginationWorkflow,
    OriginationActivities,
    State,
    assert_legal,
    is_legal,
    stub_next_state,
)
from lending.workflow.workflow import TASK_QUEUE


# ---------------------------------------------------------------------------
# Pure state machine (no Temporal)
# ---------------------------------------------------------------------------

def test_happy_path_is_all_legal():
    for frm, to in zip(HAPPY_PATH, HAPPY_PATH[1:]):
        assert is_legal(frm, to), f"{frm} → {to} should be legal"


def test_happy_path_ends_at_offer_generated():
    assert HAPPY_PATH[-1] == State.OFFER_GENERATED


# ---------------------------------------------------------------------------
# Decider-driven loop is generic: every move it proposes is legal, and it
# stops cleanly (no entry → None) rather than running off the end.
# ---------------------------------------------------------------------------

def test_decider_only_proposes_legal_moves():
    for state in State:
        nxt = stub_next_state(state)
        if nxt is not None:
            assert is_legal(state, nxt), f"decider proposed illegal {state} → {nxt}"


def test_decider_stops_at_offer_generated_and_terminals():
    assert stub_next_state(State.OFFER_GENERATED) is None
    assert stub_next_state(State.DECLINED) is None


@pytest.mark.parametrize("frm,to", [
    (State.KYC_IN_PROGRESS, State.KYC_EXCEPTION),
    (State.KYC_EXCEPTION, State.KYC_VERIFIED),
    (State.KYC_EXCEPTION, State.DECLINED),                 # human reject (not genuine)
    (State.UW_EXCEPTION, State.UNDERWRITING),              # resolve = re-assess
    (State.UW_EXCEPTION, State.DECLINED),                  # human reject (cannot underwrite)
    (State.DECISION_READY, State.DECLINED),
    (State.REFERRED, State.APPROVED),
    (State.OFFER_GENERATED, State.OFFER_EXPIRED),
])
def test_other_legal_edges(frm, to):
    assert is_legal(frm, to)


@pytest.mark.parametrize("frm,to", [
    (State.APPLICATION_SUBMITTED, State.OFFER_GENERATED),  # skip the middle
    (State.LEAD, State.UNDERWRITING),
    (State.UW_EXCEPTION, State.DECISION_READY),            # must re-assess, not skip
    (State.APPROVED, State.DECLINED),
    (State.DECLINED, State.APPROVED),                      # terminal, no exit
    (State.KYC_VERIFIED, State.KYC_IN_PROGRESS),           # no going back
])
def test_illegal_transition_rejected(frm, to):
    assert not is_legal(frm, to)
    with pytest.raises(IllegalTransition):
        assert_legal(frm, to)


# ---------------------------------------------------------------------------
# Temporal helpers
# ---------------------------------------------------------------------------

# A clean, comfortably-approvable applicant (no knockouts, high band, not income-sensitive).
CLEAN_FEATURES = {
    "age": 32,
    "monthly_income": 90_000,
    "monthly_obligations": 3_000,
    "cibil_score": 780,
    "employment_tenure_months": 60,
    "loan_amount_requested": 300_000,
    "loan_tenure_months": 36,
    "is_salaried": True,
    "has_cibil_record": True,
}


_CLEAN_DOCS = ["aadhaar_card", "pan_card", "salary_slips", "bank_statement", "form16"]


def _seed_application(repo: ApplicationRepository, features: dict | None = None) -> str:
    feats = dict(features if features is not None else CLEAN_FEATURES)
    # KYC (#19) now runs in the spine, so the applicant must have uploaded documents.
    feats.setdefault("documents", {d: {"uploaded": True, "verified": None} for d in _CLEAN_DOCS})
    app = Application(applicant=Applicant(full_name="Priya Sharma"), features=feats)
    repo.save(app)
    # Underwriting (#20) runs the consent gate (#8) before the bureau pull.
    capture_authorization(app, BUREAU_PULL_PURPOSE)
    repo.save(app)
    return app.application_id


# Fake OCR extraction for Document Intelligence (#19) — a clean, consistent doc
# set. Gross income matches CLEAN_FEATURES so the downstream decision is unchanged.
def _rec(value, ocr=0.97):
    return {"value": value, "ocr_conf": ocr}


_CLEAN_EXTRACTIONS = {
    "aadhaar_card": {"name": _rec("Priya Sharma"), "date_of_birth": _rec("1994-02-11"),
                       "aadhaar": _rec("234567890124"), "address": _rec("12 MG Road, Pune")},
    "pan_card": {"name": _rec("Priya Sharma"), "date_of_birth": _rec("1994-02-11"),
                      "pan": _rec("ABCDE1234F")},
    "salary_slips": {"name": _rec("Priya Sharma"), "employer_name": _rec("Acme Corp"),
                     "gross_monthly_income": _rec(90_000), "net_monthly_income": _rec(72_000)},
    "bank_statement": {"name": _rec("Priya Sharma"), "net_monthly_income": _rec(72_000)},
    "form16": {"name": _rec("Priya Sharma"), "pan": _rec("ABCDE1234F"),
               "employer_name": _rec("Acme Corp"), "gross_monthly_income": _rec(90_000)},
}


def _doc_extract(application_id, doc_type):
    return _CLEAN_EXTRACTIONS.get(doc_type, {})


def _doc_extract_lowconf(application_id, doc_type):
    """Same docs, but the Aadhaar is unreadable → KYC routes to exception."""
    ext = {k: dict(v) for k, v in _CLEAN_EXTRACTIONS.get(doc_type, {}).items()}
    if doc_type == "aadhaar_card":
        ext["aadhaar"] = _rec("234567890124", ocr=0.15)
    return ext


# Fake lead-qualification reasoning steps (no live Gemini in tests).
def _lead_reason(output: dict):
    return lambda context, tool_result: output


_IN_SEGMENT = {
    "segment_fit": "in_segment", "employment_type": "salaried",
    "reason_code": "PROCEED", "confidence": 0.95, "reasoning": "plausible applicant",
}
_OUT_OF_SCOPE = {
    "segment_fit": "out_of_segment", "employment_type": "unknown",
    "reason_code": "OUT_OF_SCOPE_NOT_A_LOAN", "confidence": 0.95, "reasoning": "not a loan",
}
_UNCERTAIN = {
    "segment_fit": "uncertain", "employment_type": "unknown",
    "reason_code": "INSUFFICIENT_INFO", "confidence": 0.9, "reasoning": "too little info",
}


# Bureau (#10) supplies the credit data underwriting (#20) assembles, so scenarios
# that decline/refer are driven by the bureau report — not seeded credit features.
def _bureau(report: dict | None = None):
    return make_mock_bureau_harness({HARD_INQUIRY: report or CLEAN_REPORT})


def _delivery_harnesses():
    notify, _ = make_mock_notifications_harness()
    esign, _ = make_mock_esign_harness()
    return notify, esign


def _activities(repo, audit, lead_reason=None, doc_extract=None, bureau_harness=None):
    notify, esign = _delivery_harnesses()
    return OriginationActivities(
        repo, audit,
        lead_reason=lead_reason or _lead_reason(_IN_SEGMENT),
        doc_extract=doc_extract or _doc_extract,
        bureau_harness=bureau_harness or _bureau(),
        notify_harness=notify,
        esign_harness=esign,
    )


_ALL_ACTIVITIES = (lambda a: [a.advance, a.decide, a.lead_qualify, a.verify_kyc,
                              a.underwrite, a.deliver_offer, a.record_resolution])


async def _run_with_resolution(env, repo, audit, app_id, resolution, *,
                               lead_reason=None, doc_extract=None, bureau_harness=None) -> str:
    """Start the workflow and signal a reviewer resolution (#15) — used for the
    park-and-resume paths. The signal is buffered, so the workflow consumes it
    when it reaches the parked state."""
    activities = _activities(repo, audit, lead_reason, doc_extract, bureau_harness)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[LoanOriginationWorkflow],
        activities=_ALL_ACTIVITIES(activities),
    ):
        handle = await env.client.start_workflow(
            LoanOriginationWorkflow.run, app_id,
            id=f"wf-{uuid.uuid4().hex}", task_queue=TASK_QUEUE,
        )
        await handle.signal(LoanOriginationWorkflow.resolve, resolution)
        return await handle.result()


async def _run_workflow(env, repo, audit, app_id, lead_reason=None, doc_extract=None, bureau_harness=None) -> str:
    activities = _activities(repo, audit, lead_reason, doc_extract, bureau_harness)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[LoanOriginationWorkflow],
        activities=_ALL_ACTIVITIES(activities),
    ):
        return await env.client.execute_workflow(
            LoanOriginationWorkflow.run,
            app_id,
            id=f"wf-{uuid.uuid4().hex}",
            task_queue=TASK_QUEUE,
        )


# ---------------------------------------------------------------------------
# End-to-end through the workflow
# ---------------------------------------------------------------------------

async def test_clean_applicant_reaches_offer_generated():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)  # clean → approves

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env, repo, audit, app_id)

    assert result == State.OFFER_GENERATED.value
    app = repo.get(app_id)
    assert app.workflow_state == State.OFFER_GENERATED.value
    # The real decision was recorded (no longer a stub)
    assert app.decision is not None
    assert app.decision.disposition.value == "approve"
    assert app.decision.source == "engine"
    # Decision QA + offer delivery (#23) produced a real offer letter with all terms.
    letter = app.features["offer_letter"]
    assert letter["sanctioned_amount"] > 0
    assert letter["emi"] > 0 and letter["tenure_months"] > 0
    assert letter["total_amount_payable"] > letter["sanctioned_amount"]
    assert letter["valid_until"]


async def test_knockout_applicant_is_declined():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    # Underwriting sources the score from the bureau → a low-score report knocks out.
    low_score = {**CLEAN_REPORT, "score": 600}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env, repo, audit, app_id, bureau_harness=_bureau(low_score))

    assert result == State.DECLINED.value
    decision = repo.get(app_id).decision
    assert decision.disposition.value == "decline"
    assert "LOW_CIBIL" in decision.reason_codes


async def test_referred_parks_then_underwriter_resolves():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    # High obligations → DTI soft hit → REFER → parks for an underwriter (#15).
    high_debt = {**CLEAN_REPORT, "total_monthly_obligations": 60_000.0}
    resolution = {"to_state": "DECLINED", "reviewer": "u1", "reason_code": "MANUAL_DECLINE"}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_resolution(env, repo, audit, app_id, resolution,
                                            bureau_harness=_bureau(high_debt))

    assert result == State.DECLINED.value
    # the human action is audited with the reviewer's identity
    ha = [e for e in audit.reconstruct(app_id) if e.event_type == "human_action"]
    assert ha and ha[-1].actor == "underwriter:u1" and ha[-1].payload["to"] == "DECLINED"


async def test_out_of_scope_lead_declined_early():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env, repo, audit, app_id, lead_reason=_lead_reason(_OUT_OF_SCOPE))

    # Filtered at Step 2 — never reaches the decision stage.
    assert result == State.LEAD_DECLINED.value
    assert repo.get(app_id).decision is None
    assert len([e for e in audit.reconstruct(app_id) if e.event_type == "decision"]) == 0


async def test_uncertain_lead_parks_then_resolves_to_declined():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    resolution = {"to_state": "LEAD_DECLINED", "reviewer": "u1", "reason_code": "SPAM"}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_resolution(env, repo, audit, app_id, resolution,
                                            lead_reason=_lead_reason(_UNCERTAIN))

    # Parked at LEAD_EXCEPTION, the reviewer rejects → LEAD_DECLINED.
    assert result == State.LEAD_DECLINED.value
    assert repo.get(app_id).decision is None


async def test_low_confidence_kyc_parks_then_resumes_to_offer():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    # KYC_EXCEPTION (unreadable Aadhaar) → reviewer re-verifies → resume the flow.
    resolution = {"to_state": "KYC_VERIFIED", "reviewer": "u1", "reason_code": "DOC_REVERIFIED"}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_resolution(env, repo, audit, app_id, resolution,
                                            doc_extract=_doc_extract_lowconf)

    # Resolving KYC_EXCEPTION → KYC_VERIFIED resumes underwriting → decision → offer.
    assert result == State.OFFER_GENERATED.value
    assert repo.get(app_id).decision.disposition.value == "approve"
    ha = [e for e in audit.reconstruct(app_id) if e.event_type == "human_action"]
    assert ha and ha[-1].payload["from"] == "KYC_EXCEPTION" and ha[-1].payload["to"] == "KYC_VERIFIED"


class _FlakyBureau:
    """Thin on the first pull (→ UW_EXCEPTION), healthy on the re-pull — so resolving
    the exception back to UNDERWRITING re-assembles the inputs and proceeds."""
    def __init__(self):
        self.calls = 0

    def call(self, _request):
        from types import SimpleNamespace
        self.calls += 1
        data = ({"score": None, "has_record": False, "total_monthly_obligations": 0.0,
                 "tradelines": [], "report_id": "THIN"} if self.calls == 1 else CLEAN_REPORT)
        return SimpleNamespace(data=data)


async def test_kyc_exception_rejected_declines():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    # KYC_EXCEPTION (unreadable docs) → reviewer rejects as not genuine → DECLINED.
    resolution = {"to_state": "DECLINED", "reviewer": "u1",
                  "reason_code": "DOC_NOT_GENUINE", "note": "Forged payslip."}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_resolution(env, repo, audit, app_id, resolution,
                                            doc_extract=_doc_extract_lowconf)

    assert result == State.DECLINED.value
    ha = [e for e in audit.reconstruct(app_id) if e.event_type == "human_action"]
    assert ha and ha[-1].payload["from"] == "KYC_EXCEPTION" and ha[-1].payload["to"] == "DECLINED"


async def test_uw_exception_resolution_reruns_underwriting():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    # First underwriting pass exceptions (thin file); the reviewer re-runs the
    # assessment, the re-pull is healthy, and it proceeds to a decision → offer.
    resolution = {"to_state": "UNDERWRITING", "reviewer": "u1",
                  "reason_code": "DATA_SUPPLEMENTED", "note": "Re-pulled bureau."}

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_with_resolution(env, repo, audit, app_id, resolution,
                                            bureau_harness=_FlakyBureau())

    # Re-assessment ran (not a straight skip to the decision) and reached an offer.
    assert result == State.OFFER_GENERATED.value
    trail = audit.reconstruct(app_id)
    uw = [e for e in trail if e.payload.get("agent") == "underwriting"]
    assert len(uw) == 2                       # assessed, then re-assessed
    ha = [e for e in trail if e.event_type == "human_action"]
    assert ha and ha[-1].payload["from"] == "UW_EXCEPTION" and ha[-1].payload["to"] == "UNDERWRITING"


async def test_each_transition_emits_exactly_one_audit_event():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        await _run_workflow(env, repo, audit, app_id)

    trail = audit.reconstruct(app_id)
    transitions = [e for e in trail if e.event_type == "state_transition"]
    # One event per hop along the happy path (decision routes to APPROVED → OFFER)
    assert len(transitions) == len(HAPPY_PATH) - 1
    expected = [
        {"from": frm.value, "to": to.value}
        for frm, to in zip(HAPPY_PATH, HAPPY_PATH[1:])
    ]
    assert [e.payload for e in transitions] == expected
    assert transitions[-1].payload["to"] == State.OFFER_GENERATED.value
    # Exactly one DECISION event was also recorded
    assert len([e for e in trail if e.event_type == "decision"]) == 1


# ---------------------------------------------------------------------------
# Replay — proves deterministic crash recovery
# ---------------------------------------------------------------------------

async def test_workflow_replay_is_deterministic():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app_id = _seed_application(repo)
    activities = _activities(repo, audit)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[LoanOriginationWorkflow],
            activities=_ALL_ACTIVITIES(activities),
        ):
            handle = await env.client.start_workflow(
                LoanOriginationWorkflow.run,
                app_id,
                id=f"wf-{uuid.uuid4().hex}",
                task_queue=TASK_QUEUE,
            )
            await handle.result()
            history = await handle.fetch_history()

    # Replaying the recorded history against the workflow code must not raise
    # (any non-determinism would). This is the crash-recovery guarantee.
    await Replayer(workflows=[LoanOriginationWorkflow]).replay_workflow(history)
