"""
Underwriting Agent (#20, Step at UNDERWRITING) — assemble the decision inputs.

Runs after KYC. Its job is to gather and ground the inputs the deterministic
decision engine (#18) needs, **not** to make the credit decision (§2.1):

  1. CONSENT GATE (#8) — enforce Layer-1 + mint Layer-2 before any bureau pull.
  2. BUREAU PULL (#10) — idempotent hard inquiry → score + obligations + tradelines.
  3. ASSEMBLE FEATURES — combine bureau credit data with stated/verified data
     (income from KYC, employment, loan ask) into the engine's ApplicantFeatures.
  4. READ-ONLY ENGINE PREVIEW — call Rules + Scorecard read-only to produce a
     cashflow / explainability summary (DTI, score, band, hint, reason codes).
     The engines are pure; the agent never mutates engine state and never writes
     `application.decision` — that is the decision step's job (#18).
  5. Persist the assembled features so the decision is reproducible.

Thin file (no bureau record) or a data gap (a required engine input missing) →
`UW_EXCEPTION` for human review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from lending.adapters import pull_bureau
from lending.audit import AuditStore, EventType
from lending.consent import ConsentError, enforce_consent
from lending.governance import active_version_set
from lending.rules_engine import ApplicantFeatures, dti_ratio, evaluate
from lending.scorecard import score

# The consent purpose underwriting authorizes before the hard inquiry.
BUREAU_PULL_PURPOSE = "bureau_pull"

# The engine inputs the agent assembles (ApplicantFeatures fields).
_ENGINE_FIELDS = (
    "age", "monthly_income", "monthly_obligations", "cibil_score",
    "employment_tenure_months", "loan_amount_requested", "loan_tenure_months",
    "is_salaried", "has_cibil_record",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _age_from_dob(dob: Optional[str], today: date) -> Optional[int]:
    """Derive age from a date-of-birth string (onboarding collects DOB, not age).
    Handles ISO (YYYY-MM-DD) and common Indian (DD-MM-YYYY / DD/MM/YYYY) forms."""
    if not dob:
        return None
    digits = re.findall(r"\d+", str(dob))
    year = next((int(d) for d in digits if len(d) == 4 and 1900 <= int(d) <= today.year), None)
    if year is None:
        return None
    age = today.year - year
    return age if 0 < age < 120 else None


@dataclass(frozen=True)
class UnderwritingResult:
    status: str                               # "completed" | "exception"
    summary: Optional[dict] = None            # cashflow / explainability summary
    engine_inputs: Optional[dict] = None      # assembled ApplicantFeatures (reproducibility)
    reasons: list = field(default_factory=list)  # exception reasons


def assemble_features(application, report, *, today: Optional[date] = None) -> tuple[Optional[ApplicantFeatures], list[str]]:
    """Combine bureau credit data with stated/verified application data into the
    engine's ApplicantFeatures. Returns (features, []) or (None, missing_fields).
    Age is taken from features if present, else derived from the applicant's DOB
    (onboarding collects DOB, not age)."""
    feats = application.features or {}
    income = feats.get("monthly_income") or feats.get("gross_monthly_income")
    is_salaried = feats.get("is_salaried")
    if is_salaried is None and feats.get("employment_type"):
        # Case/format-insensitive: "Salaried", "SALARIED", "salaried employee" all
        # count; "self_employed" / "unemployed" / "non-salaried" do not (#43).
        is_salaried = str(feats["employment_type"]).strip().lower().startswith("salaried")

    age = feats.get("age")
    if age is None:
        age = _age_from_dob(getattr(application.applicant, "date_of_birth", None), today or date.today())

    candidate = {
        # credit data — sourced from the bureau (authoritative)
        "cibil_score": report.score,
        "monthly_obligations": report.total_monthly_obligations,
        "has_cibil_record": report.has_record,
        # stated / verified data — from the application (KYC, onboarding)
        "age": age,
        "monthly_income": income,
        "employment_tenure_months": feats.get("employment_tenure_months"),
        "loan_amount_requested": feats.get("loan_amount_requested"),
        "loan_tenure_months": feats.get("loan_tenure_months"),
        "is_salaried": is_salaried,
    }
    missing = [k for k, v in candidate.items() if v is None]
    if missing:
        return None, missing
    return ApplicantFeatures(**candidate), []


def _build_summary(features: ApplicantFeatures, report) -> dict:
    """Read-only engine preview → a cashflow / explainability summary. Does NOT
    decide (no disposition is bound here); the decision step (#18) does that."""
    vs = active_version_set()
    rules_result = evaluate(features, vs.rules)
    score_result = score(features, vs.scorecard)
    # Post-loan DTI — the same definition the HIGH_DTI rule judges (incl. the
    # prospective EMI), so the surfaced number agrees with the decision.
    dti = dti_ratio(features) if features.monthly_income else None
    return {
        "dti": round(dti, 4) if dti is not None else None,
        "bureau_score": report.score,
        "score": score_result.score,
        "band": score_result.band.value,
        "disposition_hint": rules_result.disposition_hint.value,
        "reason_codes": [h.reason_code for h in rules_result.policy_hits],
        "monthly_income": features.monthly_income,
        "monthly_obligations": features.monthly_obligations,
        "tradelines_count": len(report.tradelines),
        "version_set": vs.model_dump(),       # pins versions → reproducible decision
    }


def underwrite(
    repository,
    audit: AuditStore,
    application_id: str,
    *,
    bureau_harness,
    now: Optional[datetime] = None,
) -> UnderwritingResult:
    """Run underwriting for an application. Records one audited reasoning event and
    returns completed (→ DECISION_READY) or exception (→ UW_EXCEPTION)."""
    application = repository.get(application_id)
    if application is None:
        raise ValueError(f"unknown application: {application_id!r}")

    # 1. Consent gate (#8) — missing/withdrawn/wrong-purpose consent is a data gap.
    try:
        enforce_consent(application, BUREAU_PULL_PURPOSE, audit, now=now)
    except ConsentError as err:
        return _exception(repository, audit, application, [f"consent:{err}"], now)

    # 2. Bureau pull (#10) — idempotent hard inquiry.
    report = pull_bureau(bureau_harness, application_id)

    # 3. Thin file → exception.
    if not report.has_record:
        return _exception(repository, audit, application, ["thin_file"], now)

    # 4. Assemble engine inputs; a data gap → exception.
    features, missing = assemble_features(application, report, today=(now or _utcnow()).date())
    if features is None:
        return _exception(repository, audit, application,
                          [f"data_gap:{m}" for m in missing], now)

    # 5. Read-only engine preview → explainability summary.
    summary = _build_summary(features, report)
    engine_inputs = {k: getattr(features, k) for k in _ENGINE_FIELDS}

    # Persist assembled inputs (so the decision is reproducible) + the summary.
    feats = dict(application.features or {})
    feats.update(engine_inputs)
    feats["underwriting_summary"] = summary
    application.features = feats
    application.updated_at = now or _utcnow()
    repository.save(application)

    audit.append(
        application_id, EventType.AGENT_REASONING,
        {"agent": "underwriting", "status": "completed", "summary": summary,
         "engine_inputs": engine_inputs},
        actor="agent:underwriting",
    )
    return UnderwritingResult("completed", summary=summary, engine_inputs=engine_inputs)


def _exception(repository, audit, application, reasons: list[str], now) -> UnderwritingResult:
    """Record a UW_EXCEPTION reasoning event. Never writes application.decision."""
    application.updated_at = now or _utcnow()
    repository.save(application)
    audit.append(
        application.application_id, EventType.AGENT_REASONING,
        {"agent": "underwriting", "status": "exception", "reasons": reasons},
        actor="agent:underwriting",
    )
    return UnderwritingResult("exception", reasons=reasons)
