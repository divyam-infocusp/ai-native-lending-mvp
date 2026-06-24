"""
Decision assembly (#18, §16.x) — the decision-of-record.

This is the seam where the deterministic core finally composes:
  rules engine (#3) + scorecard (#4) + income sensitivity (#12) → outcome,
  reason codes, rendered explanation (#17), stamped with the version set (#7).

`decide()` is pure (no I/O) — it computes the Decision from features.
`record_decision()` persists it to the LOS and appends it to the audit trail.
`reconstruct_decision()` rebuilds the issued Decision from the audit trail.

Outcome policy (deterministic, never an LLM — §2.1):
  - hard knockout                          → DECLINE
  - not lendable (band X)                  → DECLINE
  - soft policy hit (escalate)             → REFER
  - income-haircut sensitive (§16.8)       → REFER
  - otherwise                              → APPROVE
"""
from __future__ import annotations

from datetime import datetime, timezone

from lending.audit import EventType
from lending.explanation import build_context, render
from lending.governance import VersionSet, active_version_set, validate_version_set
from lending.policy import SCORECARD_POLICY
from lending.pricing import income_sensitivity
from lending.rules_engine import ApplicantFeatures, DispositionHint, evaluate
from lending.los.schema import Decision, Disposition
from lending.scorecard import RiskBand, score


def decide(
    features: ApplicantFeatures,
    version_set: VersionSet | None = None,
    language: str = "en",
) -> Decision:
    """Compose the deterministic engines into a decision-of-record. Pure."""
    vs = version_set or active_version_set()
    validate_version_set(vs)  # no decision without a complete, valid version stamp (#7)

    rules_result = evaluate(features, vs.rules)
    score_result = score(features, vs.scorecard)
    reason_codes = [hit.reason_code for hit in rules_result.policy_hits]
    sensitivity_record = None

    if rules_result.disposition_hint == DispositionHint.DECLINE:
        disposition = Disposition.DECLINE
    elif score_result.band == RiskBand.X:
        disposition = Disposition.DECLINE
        if not reason_codes:
            reason_codes = ["LOW_SCORE"]
    elif rules_result.disposition_hint == DispositionHint.ESCALATE:
        disposition = Disposition.REFER
    else:
        # Clean so far: stress-test affordability with an income-haircut re-score
        # (§16.8) — the gate unique to decision assembly. Record it either way, so
        # the audit trail shows *why* a clean-looking applicant was referred (or
        # that they were stress-tested and the band held).
        sensitivity = income_sensitivity(
            features, scorecard_version=vs.scorecard, pricing_version=vs.pricing
        )
        sensitivity_record = {
            "sensitive": sensitivity.sensitive,
            "original_band": sensitivity.original_band.value,
            "stressed_band": sensitivity.stressed_band.value,
            "haircut_pct": int(SCORECARD_POLICY[vs.scorecard]["income_haircut_pct"] * 100),
        }
        if sensitivity.sensitive:
            disposition = Disposition.REFER
            reason_codes = ["INCOME_SENSITIVE"]  # self-justifying refer (carries a reason + explanation)
        else:
            disposition = Disposition.APPROVE

    # Numbers for the templates; fold in the sensitivity figures for INCOME_SENSITIVE.
    context = build_context(vars(features), vs.rules)
    if sensitivity_record:
        context = {**context, **sensitivity_record}
    explanation_text = render(reason_codes, language, context).text if reason_codes else ""

    return Decision(
        disposition=disposition,
        source="engine",
        reason_codes=reason_codes,
        rules_version=vs.rules,
        scorecard_version=vs.scorecard,
        score=score_result.score,
        band=score_result.band.value,
        version_set=vs,
        explanation=explanation_text,
        sensitivity=sensitivity_record,
    )


def record_decision(repository, audit, application_id: str, decision: Decision) -> Decision:
    """Persist the decision onto the application and append it to the audit trail."""
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")
    application.decision = decision
    application.updated_at = datetime.now(timezone.utc)
    repository.save(application)
    audit.append(
        application_id,
        EventType.DECISION,
        decision.model_dump(mode="json"),
        actor=decision.source,
    )
    return decision


def apply_override(repository, audit, application_id: str, *, disposition: Disposition,
                   reviewer: str, reason_code: str) -> Decision:
    """Record a reviewer's soft override as the new decision-of-record (§16.10):
    the disposition becomes the human's, `source = underwriter:<id>`, and the
    engine's pinned versions/score/band/explanation are preserved. The original
    engine decision stays in the audit trail (it was recorded when first decided)."""
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")
    engine = application.decision
    override = Decision(
        disposition=disposition,
        source=f"underwriter:{reviewer}",
        reason_codes=[reason_code],
        rules_version=engine.rules_version if engine else None,
        scorecard_version=engine.scorecard_version if engine else None,
        score=engine.score if engine else None,
        band=engine.band if engine else None,
        version_set=engine.version_set if engine else None,
        explanation=engine.explanation if engine else None,
        sensitivity=engine.sensitivity if engine else None,
    )
    application.decision = override
    application.updated_at = datetime.now(timezone.utc)
    repository.save(application)
    audit.append(application_id, EventType.DECISION, override.model_dump(mode="json"), actor=override.source)
    return override


def reconstruct_decision(audit, application_id: str) -> Decision | None:
    """Rebuild the issued decision-of-record from the audit trail (the latest
    DECISION event). Returns None if no decision was ever recorded."""
    events = audit.reconstruct(application_id)
    decision_events = [e for e in events if e.event_type == EventType.DECISION.value]
    if not decision_events:
        return None
    return Decision.model_validate(decision_events[-1].payload)
