"""
Isolation tests for the Rules Engine (#3).

Covers: hard knockouts, soft policy hits, clean approval, DTI boundary,
multiple soft failures, unknown version guard.
"""
import pytest
from lending.rules_engine import ApplicantFeatures, DispositionHint, evaluate

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CLEAN = ApplicantFeatures(
    age=30,
    monthly_income=50_000,
    monthly_obligations=5_000,
    cibil_score=720,
    employment_tenure_months=24,
    loan_amount_requested=300_000,
    loan_tenure_months=36,
    is_salaried=True,
    has_cibil_record=True,
)


def tweak(**kwargs) -> ApplicantFeatures:
    d = {f: getattr(CLEAN, f) for f in CLEAN.__dataclass_fields__}
    d.update(kwargs)
    return ApplicantFeatures(**d)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_clean_applicant_approves():
    result = evaluate(CLEAN)
    assert result.disposition_hint == DispositionHint.APPROVE
    assert result.policy_hits == []
    assert all(r.passed for r in result.rule_results)


# ---------------------------------------------------------------------------
# Hard knockouts — each must produce DECLINE and fire the right reason_code
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("override,expected_reason", [
    ({"age": 18},                   "UNDERAGE"),
    ({"age": 65},                   "OVERAGE"),
    ({"is_salaried": False},        "NOT_SALARIED"),
    ({"has_cibil_record": False},   "NO_CIBIL_RECORD"),
    ({"cibil_score": 600},          "LOW_CIBIL"),
    ({"monthly_income": 10_000},    "INSUFFICIENT_INCOME"),
    ({"employment_tenure_months": 3}, "SHORT_EMPLOYMENT"),
])
def test_hard_knockout(override, expected_reason):
    f = tweak(**override)
    result = evaluate(f)
    assert result.disposition_hint == DispositionHint.DECLINE
    reason_codes = [h.reason_code for h in result.policy_hits]
    assert expected_reason in reason_codes


def test_knockout_short_circuits():
    """After a hard knockout, later rules must not appear in rule_results."""
    f = tweak(age=18)  # R01 fires → should stop before R02 onwards
    result = evaluate(f)
    evaluated_ids = {r.rule_id for r in result.rule_results}
    # R01 must be present; R02 and beyond must NOT be present
    assert "R01_MIN_AGE" in evaluated_ids
    assert "R02_MAX_AGE" not in evaluated_ids


# ---------------------------------------------------------------------------
# Soft policy failures → ESCALATE
# ---------------------------------------------------------------------------

def test_high_dti_escalates():
    # DTI = (obligations + new_emi) / income
    # Set obligations so DTI > 0.50
    f = tweak(monthly_obligations=30_000)
    result = evaluate(f)
    assert result.disposition_hint == DispositionHint.ESCALATE
    reason_codes = [h.reason_code for h in result.policy_hits]
    assert "HIGH_DTI" in reason_codes


def test_loan_amount_exceeds_limit_escalates():
    f = tweak(loan_amount_requested=3_000_000)
    result = evaluate(f)
    assert result.disposition_hint == DispositionHint.ESCALATE
    reason_codes = [h.reason_code for h in result.policy_hits]
    assert "LOAN_AMOUNT_EXCEEDS_LIMIT" in reason_codes


def test_multiple_soft_failures_all_captured():
    f = tweak(monthly_obligations=30_000, loan_amount_requested=3_000_000)
    result = evaluate(f)
    assert result.disposition_hint == DispositionHint.ESCALATE
    reason_codes = {h.reason_code for h in result.policy_hits}
    assert {"HIGH_DTI", "LOAN_AMOUNT_EXCEEDS_LIMIT"} == reason_codes


# ---------------------------------------------------------------------------
# Policy overrides
# ---------------------------------------------------------------------------

def test_policy_override_tightens_cibil():
    f = tweak(cibil_score=680)
    # Default min is 650 → passes; override to 700 → fails
    result = evaluate(f, policy_overrides={"min_cibil_score": 700})
    assert result.disposition_hint == DispositionHint.DECLINE
    assert "LOW_CIBIL" in [h.reason_code for h in result.policy_hits]


def test_policy_override_loosens_dti():
    f = tweak(monthly_obligations=30_000)
    # Default max DTI 0.50 → ESCALATE; raise to 0.90 → APPROVE
    result = evaluate(f, policy_overrides={"max_dti": 0.90})
    assert result.disposition_hint == DispositionHint.APPROVE


# ---------------------------------------------------------------------------
# DTI boundary exactness
# ---------------------------------------------------------------------------

def test_dti_exactly_at_limit_passes():
    # DTI == 0.50 exactly should pass
    # obligations + emi = 0.50 * income
    # income=50000, emi=loan/tenure=300000/36≈8333, obligations=50000*0.50-8333=16667
    income = 50_000
    emi = 300_000 / 36
    obligations = income * 0.50 - emi
    f = tweak(monthly_obligations=obligations)
    result = evaluate(f)
    # Should not hit HIGH_DTI
    assert "HIGH_DTI" not in [h.reason_code for h in result.policy_hits]


# ---------------------------------------------------------------------------
# Unknown version guard
# ---------------------------------------------------------------------------

def test_unknown_version_raises():
    with pytest.raises(ValueError, match="Unknown rules_version"):
        evaluate(CLEAN, rules_version="v99")


# ---------------------------------------------------------------------------
# Result structure completeness
# ---------------------------------------------------------------------------

def test_all_rules_evaluated_on_clean():
    result = evaluate(CLEAN)
    rule_ids = [r.rule_id for r in result.rule_results]
    # All 9 rules must be evaluated (no early exit on clean case)
    assert len(rule_ids) == 9
    assert "R01_MIN_AGE" in rule_ids
    assert "R09_MAX_LOAN" in rule_ids
