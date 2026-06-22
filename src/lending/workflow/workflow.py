"""
Loan origination workflow (#13) — the durable spine.

Generic, decider-driven loop: at each state it asks "what is the next state for
this application?", validates the move, and advances — repeating until the
decider says stop. The workflow body is deterministic (it only calls a pure
decider + activities), so Temporal can replay it after a crash.

The decider is the single extension point. Real branch-point logic lives in the
loop (#18/#19/#20/#21/#23). Human-in-the-loop (#15): at the exception states and
REFERRED the workflow **parks** — it durably waits for a `resolve` signal from a
reviewer (Ops Console) and then advances to the resolved (legal) state.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import OriginationActivities
    from .statemachine import HAPPY_PATH, State, is_legal

TASK_QUEUE = "loan-origination"

# States where the workflow waits for a human decision before continuing (#15):
# the three exception states + REFERRED (borderline → underwriter approves/declines).
_PARK_STATES = frozenset({
    State.LEAD_EXCEPTION, State.KYC_EXCEPTION, State.UW_EXCEPTION, State.REFERRED,
})

# Stub next-state policy, derived from the canonical happy path so there is one
# source of truth. A state with no entry here ends the run (e.g. OFFER_GENERATED
# for the stub, or a terminal state like DECLINED).
_STUB_NEXT: dict[State, State] = {frm: to for frm, to in zip(HAPPY_PATH, HAPPY_PATH[1:])}


def stub_next_state(current: State) -> State | None:
    """STUB decider: pick the next state, or None to stop. Replace the body with
    real branch-point logic (#15/#18); the workflow loop stays unchanged."""
    return _STUB_NEXT.get(current)


@workflow.defn
class LoanOriginationWorkflow:
    def __init__(self) -> None:
        # Set by the `resolve` signal when a reviewer resolves a parked case (#15).
        self._resolution: dict | None = None

    @workflow.signal
    def resolve(self, resolution: dict) -> None:
        """Reviewer resolution for a parked case: {to_state, reviewer, reason_code}."""
        self._resolution = resolution

    @workflow.run
    async def run(self, application_id: str) -> str:
        current = HAPPY_PATH[0]
        while True:
            if current == State.LEAD:
                # Lead Qualification Agent (#21) replaces the stubbed LEAD → LEAD_QUALIFIED.
                outcome = await workflow.execute_activity(
                    OriginationActivities.lead_qualify,
                    args=[application_id],
                    start_to_close_timeout=timedelta(seconds=30),  # LLM call
                )
                next_state = State(outcome)  # LEAD_QUALIFIED | LEAD_DECLINED | LEAD_EXCEPTION
            elif current == State.KYC_IN_PROGRESS:
                # Document Intelligence Agent (#19) replaces the stubbed KYC verify.
                outcome = await workflow.execute_activity(
                    OriginationActivities.verify_kyc,
                    args=[application_id],
                    start_to_close_timeout=timedelta(seconds=60),  # OCR + scoring
                )
                next_state = State(outcome)  # KYC_VERIFIED | KYC_EXCEPTION
            elif current == State.UNDERWRITING:
                # Underwriting Agent (#20) replaces the stubbed underwriting step.
                outcome = await workflow.execute_activity(
                    OriginationActivities.underwrite,
                    args=[application_id],
                    start_to_close_timeout=timedelta(seconds=30),  # bureau pull + assembly
                )
                next_state = State(outcome)  # DECISION_READY | UW_EXCEPTION
            elif current == State.DECISION_READY:
                # Real decision engine (#18) replaces the stubbed approve.
                outcome = await workflow.execute_activity(
                    OriginationActivities.decide,
                    args=[application_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                next_state = State(outcome)  # APPROVED | DECLINED | REFERRED
            elif current == State.APPROVED:
                # Decision QA + offer delivery (#23) replaces the stubbed offer step.
                outcome = await workflow.execute_activity(
                    OriginationActivities.deliver_offer,
                    args=[application_id],
                    start_to_close_timeout=timedelta(seconds=30),  # pricing + notify + e-sign
                )
                next_state = State(outcome)  # OFFER_GENERATED
            elif current in _PARK_STATES:
                # Human-in-the-loop (#15): park durably until a reviewer resolves.
                await workflow.wait_condition(lambda: self._resolution is not None)
                resolution = self._resolution
                self._resolution = None
                to_state = State(resolution["to_state"])
                if not is_legal(current, to_state):
                    continue  # ignore an illegal resolution; keep waiting
                await workflow.execute_activity(
                    OriginationActivities.record_resolution,
                    args=[application_id, current.value, to_state.value,
                          resolution.get("reviewer", "unknown"), resolution.get("reason_code")],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                next_state = to_state
            else:
                next_state = stub_next_state(current)
            if next_state is None:
                break  # done (offer generated, or a terminal end)
            result = await workflow.execute_activity(
                OriginationActivities.advance,
                args=[application_id, current.value, next_state.value],
                start_to_close_timeout=timedelta(seconds=10),
            )
            current = State(result)
        return current.value
