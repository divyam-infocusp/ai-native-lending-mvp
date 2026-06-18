"""
Loan origination workflow (#13) — the durable spine.

Generic, decider-driven loop: at each state it asks "what is the next state for
this application?", validates the move, and advances — repeating until the
decider says stop. The workflow body is deterministic (it only calls a pure
decider + activities), so Temporal can replay it after a crash.

The decider is the single extension point. Today it is **stubbed** to follow the
clean happy path to OFFER_GENERATED. Real branch-point logic slots in here
without touching the loop:
  - KYC_IN_PROGRESS → KYC_VERIFIED | KYC_EXCEPTION   (confidence #5; park via signal #15)
  - UNDERWRITING    → DECISION_READY | UW_EXCEPTION  (data completeness #15)
  - DECISION_READY  → APPROVED | DECLINED | REFERRED (rules + scorecard #18)

Human-wait via signals (parking at *_EXCEPTION) is wired in #15.
"""
from __future__ import annotations

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from .activities import OriginationActivities
    from .statemachine import HAPPY_PATH, State

TASK_QUEUE = "loan-origination"

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
            elif current == State.DECISION_READY:
                # Real decision engine (#18) replaces the stubbed approve.
                outcome = await workflow.execute_activity(
                    OriginationActivities.decide,
                    args=[application_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                next_state = State(outcome)  # APPROVED | DECLINED | REFERRED
            else:
                next_state = stub_next_state(current)
            if next_state is None:
                break  # done (offer generated, or a terminal/referred end)
            result = await workflow.execute_activity(
                OriginationActivities.advance,
                args=[application_id, current.value, next_state.value],
                start_to_close_timeout=timedelta(seconds=10),
            )
            current = State(result)
        return current.value
