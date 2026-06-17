"""
Temporal activities for origination (#13).

Activities are where side effects live (DB writes, audit appends) — kept out of
the workflow so the workflow stays deterministic and replayable. Each `advance`
call validates the transition, updates the LOS record's state, and appends
exactly one audited event.

DI: the activities hold the LOS repository and audit store, so the worker wires
them to a concrete engine and tests can wire an in-memory one.
"""
from __future__ import annotations

from datetime import datetime, timezone

from temporalio import activity

from lending.audit import AuditStore, EventType
from lending.decision import decide, record_decision
from lending.los import ApplicationRepository, ApplicationStatus
from lending.los.schema import Disposition
from lending.rules_engine import ApplicantFeatures

from .statemachine import State, assert_legal

# Map the engine's disposition to the §4 outcome state the workflow advances to.
_DISPOSITION_TO_STATE: dict[Disposition, State] = {
    Disposition.APPROVE: State.APPROVED,
    Disposition.DECLINE: State.DECLINED,
    Disposition.REFER: State.REFERRED,
}

# Map the fine-grained §4 state to the coarse LOS status.
_COARSE_STATUS: dict[State, ApplicationStatus] = {
    State.LEAD: ApplicationStatus.CREATED,
    State.LEAD_QUALIFIED: ApplicationStatus.IN_PROGRESS,
    State.APPLICATION_SUBMITTED: ApplicationStatus.IN_PROGRESS,
    State.KYC_IN_PROGRESS: ApplicationStatus.IN_PROGRESS,
    State.KYC_VERIFIED: ApplicationStatus.IN_PROGRESS,
    State.KYC_EXCEPTION: ApplicationStatus.EXCEPTION,
    State.UNDERWRITING: ApplicationStatus.IN_PROGRESS,
    State.DECISION_READY: ApplicationStatus.IN_PROGRESS,
    State.UW_EXCEPTION: ApplicationStatus.EXCEPTION,
    State.REFERRED: ApplicationStatus.IN_PROGRESS,
    State.APPROVED: ApplicationStatus.DECIDED,
    State.DECLINED: ApplicationStatus.DECIDED,
    State.OFFER_GENERATED: ApplicationStatus.DECIDED,
    State.OFFER_ACCEPTED: ApplicationStatus.DECIDED,
    State.OFFER_EXPIRED: ApplicationStatus.DECIDED,
}


class OriginationActivities:
    def __init__(self, repository: ApplicationRepository, audit: AuditStore) -> None:
        self._repo = repository
        self._audit = audit

    @activity.defn
    async def advance(self, application_id: str, from_state: str, to_state: str) -> str:
        # Guard: reject any move not on the §4 diagram.
        assert_legal(State(from_state), State(to_state))

        app = self._repo.get(application_id)
        if app is None:
            raise ValueError(f"unknown application: {application_id!r}")

        app.workflow_state = to_state
        app.status = _COARSE_STATUS.get(State(to_state), app.status)
        app.updated_at = datetime.now(timezone.utc)
        self._repo.save(app)

        # Exactly one audited event per transition.
        self._audit.append(
            application_id,
            EventType.STATE_TRANSITION,
            {"from": from_state, "to": to_state},
            actor="workflow",
        )
        return to_state

    @activity.defn
    async def decide(self, application_id: str) -> str:
        """Run the real decision engine (#18), persist + audit the decision-of-record,
        and return the §4 outcome state (APPROVED / DECLINED / REFERRED)."""
        application = self._repo.get(application_id)
        if application is None:
            raise ValueError(f"unknown application: {application_id!r}")
        features = ApplicantFeatures(**application.features)
        decision = decide(features)
        record_decision(self._repo, self._audit, application_id, decision)
        return _DISPOSITION_TO_STATE[decision.disposition].value
