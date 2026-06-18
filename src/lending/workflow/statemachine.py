"""
Origination state machine (§4) — pure, no Temporal, no I/O.

Defines the legal states and the legal transitions between them, plus a guard
that rejects any move not on the §4 diagram. Kept deterministic and dependency-
free so the transition rules can be unit-tested in isolation and reused by both
the workflow (#13) and later the pipeline viewer (#30).
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    LEAD = "LEAD"
    LEAD_QUALIFIED = "LEAD_QUALIFIED"
    LEAD_DECLINED = "LEAD_DECLINED"      # out-of-scope lead, declined-early (#21)
    LEAD_EXCEPTION = "LEAD_EXCEPTION"    # lead routing uncertain → human review (#21)
    APPLICATION_SUBMITTED = "APPLICATION_SUBMITTED"
    KYC_IN_PROGRESS = "KYC_IN_PROGRESS"
    KYC_VERIFIED = "KYC_VERIFIED"
    KYC_EXCEPTION = "KYC_EXCEPTION"
    UNDERWRITING = "UNDERWRITING"
    DECISION_READY = "DECISION_READY"
    UW_EXCEPTION = "UW_EXCEPTION"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"
    REFERRED = "REFERRED"
    OFFER_GENERATED = "OFFER_GENERATED"
    OFFER_ACCEPTED = "OFFER_ACCEPTED"
    OFFER_EXPIRED = "OFFER_EXPIRED"


class IllegalTransition(Exception):
    """Raised when a transition is not permitted by the §4 state machine."""


# Adjacency list — exactly the edges in the §4 diagram.
LEGAL_TRANSITIONS: dict[State, frozenset[State]] = {
    State.LEAD: frozenset({State.LEAD_QUALIFIED, State.LEAD_DECLINED, State.LEAD_EXCEPTION}),
    State.LEAD_EXCEPTION: frozenset({State.LEAD_QUALIFIED, State.LEAD_DECLINED}),  # human resolves
    State.LEAD_DECLINED: frozenset(),  # terminal
    State.LEAD_QUALIFIED: frozenset({State.APPLICATION_SUBMITTED}),
    State.APPLICATION_SUBMITTED: frozenset({State.KYC_IN_PROGRESS}),
    State.KYC_IN_PROGRESS: frozenset({State.KYC_VERIFIED, State.KYC_EXCEPTION}),
    State.KYC_EXCEPTION: frozenset({State.KYC_VERIFIED}),
    State.KYC_VERIFIED: frozenset({State.UNDERWRITING}),
    State.UNDERWRITING: frozenset({State.DECISION_READY, State.UW_EXCEPTION}),
    State.UW_EXCEPTION: frozenset({State.DECISION_READY}),
    State.DECISION_READY: frozenset({State.APPROVED, State.DECLINED, State.REFERRED}),
    State.REFERRED: frozenset({State.APPROVED, State.DECLINED}),
    State.APPROVED: frozenset({State.OFFER_GENERATED}),
    State.OFFER_GENERATED: frozenset({State.OFFER_ACCEPTED, State.OFFER_EXPIRED}),
    # terminal states
    State.DECLINED: frozenset(),
    State.OFFER_ACCEPTED: frozenset(),
    State.OFFER_EXPIRED: frozenset(),
}

# The stubbed happy path this issue (#13) drives: clean application straight to
# an offer. The decision step (DECISION_READY → APPROVED) is hard-coded here;
# the real engine wiring comes in #18.
HAPPY_PATH: tuple[State, ...] = (
    State.LEAD,
    State.LEAD_QUALIFIED,
    State.APPLICATION_SUBMITTED,
    State.KYC_IN_PROGRESS,
    State.KYC_VERIFIED,
    State.UNDERWRITING,
    State.DECISION_READY,
    State.APPROVED,
    State.OFFER_GENERATED,
)


def is_legal(from_state: State, to_state: State) -> bool:
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def assert_legal(from_state: State, to_state: State) -> None:
    if not is_legal(from_state, to_state):
        raise IllegalTransition(f"{from_state.value} → {to_state.value} is not a legal transition")
