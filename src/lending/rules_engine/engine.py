"""
Rules Engine — deterministic policy evaluation.

Policy rules are versioned dicts; version key maps to an ordered list of
Rule callables. Hard knockouts fire first; soft policy checks after.
No LLM involvement — pure arithmetic + comparisons.
"""
from dataclasses import dataclass
from typing import Callable

from lending.policy import RULES_POLICY

from .models import (
    ApplicantFeatures,
    DispositionHint,
    EvaluationResult,
    PolicyHit,
    RuleResult,
)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    reason_code: str
    is_knockout: bool  # True → single failure → DECLINE immediately
    check: Callable[[ApplicantFeatures, dict], tuple[bool, object, object]]
    # check returns (passed, actual_value, threshold_value)


def _dti(f: ApplicantFeatures) -> float:
    emi = (f.loan_amount_requested / f.loan_tenure_months) if f.loan_tenure_months > 0 else 0
    return (f.monthly_obligations + emi) / f.monthly_income if f.monthly_income > 0 else float("inf")


# ---------------------------------------------------------------------------
# Rule catalogue (v1)
# ---------------------------------------------------------------------------

_RULES_V1: list[Rule] = [
    # --- hard knockouts (non-overridable) ---
    Rule(
        rule_id="R01_MIN_AGE",
        reason_code="UNDERAGE",
        is_knockout=True,
        check=lambda f, p: (f.age >= p["min_age"], f.age, p["min_age"]),
    ),
    Rule(
        rule_id="R02_MAX_AGE",
        reason_code="OVERAGE",
        is_knockout=True,
        check=lambda f, p: (f.age <= p["max_age"], f.age, p["max_age"]),
    ),
    Rule(
        rule_id="R03_SALARIED",
        reason_code="NOT_SALARIED",
        is_knockout=True,
        check=lambda f, p: (f.is_salaried, f.is_salaried, True),
    ),
    Rule(
        rule_id="R04_CIBIL_RECORD",
        reason_code="NO_CIBIL_RECORD",
        is_knockout=True,
        check=lambda f, p: (f.has_cibil_record, f.has_cibil_record, True),
    ),
    Rule(
        rule_id="R05_MIN_CIBIL",
        reason_code="LOW_CIBIL",
        is_knockout=True,
        check=lambda f, p: (f.cibil_score >= p["min_cibil_score"], f.cibil_score, p["min_cibil_score"]),
    ),
    Rule(
        rule_id="R06_MIN_INCOME",
        reason_code="INSUFFICIENT_INCOME",
        is_knockout=True,
        check=lambda f, p: (f.monthly_income >= p["min_monthly_income"], f.monthly_income, p["min_monthly_income"]),
    ),
    Rule(
        rule_id="R07_MIN_TENURE",
        reason_code="SHORT_EMPLOYMENT",
        is_knockout=True,
        check=lambda f, p: (
            f.employment_tenure_months >= p["min_employment_months"],
            f.employment_tenure_months,
            p["min_employment_months"],
        ),
    ),
    # --- soft policy checks (overridable by underwriter) ---
    Rule(
        rule_id="R08_MAX_DTI",
        reason_code="HIGH_DTI",
        is_knockout=False,
        check=lambda f, p: (_dti(f) <= p["max_dti"], round(_dti(f), 4), p["max_dti"]),
    ),
    Rule(
        rule_id="R09_MAX_LOAN",
        reason_code="LOAN_AMOUNT_EXCEEDS_LIMIT",
        is_knockout=False,
        check=lambda f, p: (
            f.loan_amount_requested <= p["max_loan_amount"],
            f.loan_amount_requested,
            p["max_loan_amount"],
        ),
    ),
]


# Rule *logic* (the catalogue) lives in code; the *thresholds* it reads live in
# the versioned RULES_POLICY config (§16.9).
_RULE_CATALOGUE: dict[str, list[Rule]] = {
    "v1": _RULES_V1,
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def evaluate(
    features: ApplicantFeatures,
    rules_version: str = "v1",
    policy_overrides: dict | None = None,
) -> EvaluationResult:
    """
    Evaluate all rules for this version against the provided features.

    Hard knockouts short-circuit to DECLINE on first failure.
    Multiple soft failures accumulate; any soft failure → ESCALATE.
    All rules pass → APPROVE.
    """
    if rules_version not in _RULE_CATALOGUE or rules_version not in RULES_POLICY:
        raise ValueError(f"Unknown rules_version: {rules_version!r}")

    rules = _RULE_CATALOGUE[rules_version]
    policy = {**RULES_POLICY[rules_version], **(policy_overrides or {})}

    rule_results: list[RuleResult] = []
    policy_hits: list[PolicyHit] = []

    for rule in rules:
        passed, actual, threshold = rule.check(features, policy)
        rule_results.append(RuleResult(rule.rule_id, passed, actual, threshold))
        if not passed:
            policy_hits.append(PolicyHit(rule.rule_id, rule.reason_code))
            if rule.is_knockout:
                # Short-circuit: remaining rules not evaluated
                return EvaluationResult(rule_results, policy_hits, DispositionHint.DECLINE)

    if policy_hits:
        return EvaluationResult(rule_results, policy_hits, DispositionHint.ESCALATE)
    return EvaluationResult(rule_results, policy_hits, DispositionHint.APPROVE)
