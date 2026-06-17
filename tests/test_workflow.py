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

from lending.audit import AuditStore
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
    (State.DECISION_READY, State.DECLINED),
    (State.REFERRED, State.APPROVED),
    (State.OFFER_GENERATED, State.OFFER_EXPIRED),
])
def test_other_legal_edges(frm, to):
    assert is_legal(frm, to)


@pytest.mark.parametrize("frm,to", [
    (State.APPLICATION_SUBMITTED, State.OFFER_GENERATED),  # skip the middle
    (State.LEAD, State.UNDERWRITING),
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


def _seed_application(repo: ApplicationRepository, features: dict | None = None) -> str:
    app = Application(
        applicant=Applicant(full_name="Priya Sharma"),
        features=features if features is not None else dict(CLEAN_FEATURES),
    )
    repo.save(app)
    return app.application_id


async def _run_workflow(env, repo, audit, app_id) -> str:
    activities = OriginationActivities(repo, audit)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[LoanOriginationWorkflow],
        activities=[activities.advance, activities.decide],
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


async def test_knockout_applicant_is_declined():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    bad = dict(CLEAN_FEATURES, cibil_score=600)  # LOW_CIBIL hard knockout
    app_id = _seed_application(repo, bad)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env, repo, audit, app_id)

    assert result == State.DECLINED.value
    decision = repo.get(app_id).decision
    assert decision.disposition.value == "decline"
    assert "LOW_CIBIL" in decision.reason_codes


async def test_soft_hit_applicant_is_referred():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    # High existing obligations → DTI soft hit → escalate → REFER
    soft = dict(CLEAN_FEATURES, monthly_obligations=60_000)
    app_id = _seed_application(repo, soft)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        result = await _run_workflow(env, repo, audit, app_id)

    assert result == State.REFERRED.value
    assert repo.get(app_id).decision.disposition.value == "refer"


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
    activities = OriginationActivities(repo, audit)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[LoanOriginationWorkflow],
            activities=[activities.advance, activities.decide],
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
