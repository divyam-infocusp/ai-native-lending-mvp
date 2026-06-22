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

from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone

from temporalio import activity

from lending.audit import AuditStore, EventType
from lending.decision import decide, record_decision
from lending.los import ApplicationRepository, ApplicationStatus
from lending.los.schema import Disposition
from lending.rules_engine import ApplicantFeatures

from .statemachine import State, assert_legal

# The scorecard/rules inputs the decision engine consumes. `application.features`
# also carries non-scoring data (documents, extracted income aliases, loan
# purpose, …), so we select only these — never `ApplicantFeatures(**features)`.
_FEATURE_FIELDS = tuple(f.name for f in dataclass_fields(ApplicantFeatures))

# Map the engine's disposition to the §4 outcome state the workflow advances to.
_DISPOSITION_TO_STATE: dict[Disposition, State] = {
    Disposition.APPROVE: State.APPROVED,
    Disposition.DECLINE: State.DECLINED,
    Disposition.REFER: State.REFERRED,
}

# Map the lead-qualification outcome to the §4 state the workflow advances to.
_QUALIFY_STATUS_TO_STATE: dict[str, State] = {
    "qualified": State.LEAD_QUALIFIED,
    "declined_early": State.LEAD_DECLINED,
    "manual_review": State.LEAD_EXCEPTION,
}

# Map the Document Intelligence outcome to the §4 KYC state.
_KYC_STATUS_TO_STATE: dict[str, State] = {
    "verified": State.KYC_VERIFIED,
    "exception": State.KYC_EXCEPTION,
}

# Map the fine-grained §4 state to the coarse LOS status.
_COARSE_STATUS: dict[State, ApplicationStatus] = {
    State.LEAD: ApplicationStatus.CREATED,
    State.LEAD_QUALIFIED: ApplicationStatus.IN_PROGRESS,
    State.LEAD_DECLINED: ApplicationStatus.DECIDED,
    State.LEAD_EXCEPTION: ApplicationStatus.EXCEPTION,
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
    def __init__(
        self,
        repository: ApplicationRepository,
        audit: AuditStore,
        lead_reason=None,
        doc_extract=None,
    ) -> None:
        self._repo = repository
        self._audit = audit
        # Injected fake reasoning step for the lead-qualification agent in tests;
        # None → the agent uses its default (Gemini) at runtime.
        self._lead_reason = lead_reason
        # Injected document extractor (OCR adapter #9) for Document Intelligence;
        # tests inject a fake. None → must be supplied before KYC runs (no real
        # OCR adapter exists until #9).
        self._doc_extract = doc_extract

    @activity.defn
    async def lead_qualify(self, application_id: str) -> str:
        """Run the Lead Qualification Agent (#21) and return the §4 outcome state
        (LEAD_QUALIFIED / LEAD_DECLINED / LEAD_EXCEPTION)."""
        # Lazy import: keeps LangGraph out of the Temporal workflow sandbox's import
        # graph (activities run outside the sandbox, so importing here is safe).
        from lending.agents import qualify_lead

        result = qualify_lead(self._repo, self._audit, application_id, reason=self._lead_reason)
        return _QUALIFY_STATUS_TO_STATE[result.status].value

    @activity.defn
    async def verify_kyc(self, application_id: str) -> str:
        """Run the Document Intelligence Agent (#19): extract + cross-check + ground
        confidence, persist the verified profile, return the §4 KYC outcome state
        (KYC_VERIFIED / KYC_EXCEPTION)."""
        # Lazy import: keep the agent layer out of the Temporal workflow sandbox.
        from lending.agents import verify_documents

        if self._doc_extract is None:
            raise ValueError("no document extractor wired (OCR adapter #9 / inject doc_extract)")
        result = verify_documents(
            self._repo, self._audit, application_id, extract=self._doc_extract
        )
        return _KYC_STATUS_TO_STATE[result.status].value

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
        feats = application.features or {}
        missing = [f for f in _FEATURE_FIELDS if f not in feats]
        if missing:
            raise ValueError(f"missing scoring features for {application_id!r}: {missing}")
        features = ApplicantFeatures(**{f: feats[f] for f in _FEATURE_FIELDS})
        decision = decide(features)
        record_decision(self._repo, self._audit, application_id, decision)
        return _DISPOSITION_TO_STATE[decision.disposition].value
