"""
Tests for cashflow / bank-statement cross-validation (#53 Phase 1).

Covers the deterministic core (income + recurring-obligation derivation), the
bank-vs-bureau obligations reconciliation, the REFER routing it triggers in the
decision engine, the income cross-check through Document Intelligence, and the
onboarding wiring that makes bank_statement an accepted *optional* document.

"LLM perceives, code decides": every figure here is computed by deterministic code
from labeled transactions — no model is involved in any of these tests.
"""
import pytest

import lending.policy as policy
from lending.agents.document_intelligence import evaluate
from lending.agents.onboarding import (
    REQUIRED_DOCUMENTS,
    missing_fields,
    register_document,
)
from lending.agents.underwriting import reconcile_obligations
from lending.cashflow import Transaction, TxnCategory, TxnDirection, analyze
from lending.decision import decide
from lending.explanation.renderer import verify_faithful
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.rules_engine import ApplicantFeatures


def _tx(date, desc, amount, direction, category, quote="q"):
    return Transaction(date=date, description=desc, amount=amount,
                       direction=direction, category=category, source_quote=quote)


def _emi_statement():
    """A 3-month statement with regular salary and two recurring EMI streams whose
    narrations vary month to month (ref numbers + month names) — the EMI path the
    real ₹0-obligation specimen could not exercise."""
    months = [("01", "JAN"), ("02", "FEB"), ("03", "MAR")]
    txns = []
    for m, mon in months:
        txns += [
            _tx(f"2026-{m}-01", f"NEFT/SAL/TECHNOVA/REF{m}88", 71500, TxnDirection.CREDIT, TxnCategory.SALARY),
            _tx(f"2026-{m}-05", f"ACH/HDFC/HOMELOAN/00{m}45/{mon}", 25000, TxnDirection.DEBIT, TxnCategory.LOAN_EMI),
            _tx(f"2026-{m}-07", f"NACH/BAJAJFIN/EMI/{m}{mon}", 3000, TxnDirection.DEBIT, TxnCategory.LOAN_EMI),
            _tx(f"2026-{m}-12", f"POS/DMART/{m}", 1800, TxnDirection.DEBIT, TxnCategory.DISCRETIONARY),
        ]
    return txns


# ---------------------------------------------------------------------------
# Core: net monthly income
# ---------------------------------------------------------------------------

def test_net_monthly_income_is_mean_of_monthly_salary():
    a = analyze(_emi_statement())
    assert a.net_monthly_income.value == 71500.0
    assert a.net_monthly_income.confidence == 1.0          # salary every covered month
    assert a.net_monthly_income.flags == ()


def test_net_monthly_income_irregular_lowers_confidence_and_flags():
    # Salary in Jan & Mar only; a Feb debit keeps the span at 3 months.
    txns = [
        _tx("2026-01-01", "SAL", 70000, TxnDirection.CREDIT, TxnCategory.SALARY),
        _tx("2026-02-09", "SWIGGY", 500, TxnDirection.DEBIT, TxnCategory.DISCRETIONARY),
        _tx("2026-03-01", "SAL", 70000, TxnDirection.CREDIT, TxnCategory.SALARY),
    ]
    a = analyze(txns)
    assert a.months_covered == 3
    assert a.net_monthly_income.confidence == pytest.approx(2 / 3, abs=1e-3)
    assert "IRREGULAR_SALARY" in a.net_monthly_income.flags


def test_net_monthly_income_none_when_no_salary():
    txns = [_tx("2026-01-05", "ACH/EMI", 5000, TxnDirection.DEBIT, TxnCategory.LOAN_EMI)]
    assert analyze(txns).net_monthly_income is None


def test_income_provenance_scales_confidence():
    a = analyze(_emi_statement(), income_provenance=0.5)
    assert a.net_monthly_income.confidence == 0.5


# ---------------------------------------------------------------------------
# Core: recurring monthly obligations
# ---------------------------------------------------------------------------

def test_obligations_detect_recurring_streams_across_varying_narrations():
    a = analyze(_emi_statement())
    # ₹25k home loan + ₹3k BNPL, both recurring all 3 months despite varying refs.
    assert a.monthly_obligations.value == 28000.0
    assert a.monthly_obligations.confidence == 1.0
    payees = {b["payee"] for b in a.monthly_obligations.basis}
    assert payees == {"achhdfchomeloan", "nachbajajfinemi"}


def test_obligations_exclude_one_off_debit():
    txns = _emi_statement()
    # A single large EMI-looking debit appearing once must NOT count.
    txns.append(_tx("2026-02-15", "ACH/ONE/TIME/PREPAY", 40000, TxnDirection.DEBIT, TxnCategory.LOAN_EMI))
    a = analyze(txns)
    assert a.monthly_obligations.value == 28000.0          # unchanged — prepay excluded


def test_obligations_confident_zero_when_no_emis():
    txns = [
        _tx("2026-01-01", "SAL", 60000, TxnDirection.CREDIT, TxnCategory.SALARY),
        _tx("2026-01-02", "RENT", 15000, TxnDirection.DEBIT, TxnCategory.RENT),
        _tx("2026-02-01", "SAL", 60000, TxnDirection.CREDIT, TxnCategory.SALARY),
    ]
    ob = analyze(txns).monthly_obligations
    assert ob.value == 0.0 and ob.confidence == 1.0        # confident zero, not None


def test_months_covered_is_inclusive_span():
    txns = [
        _tx("2026-01-15", "x", 1, TxnDirection.DEBIT, TxnCategory.OTHER),
        _tx("2026-04-15", "y", 1, TxnDirection.DEBIT, TxnCategory.OTHER),
    ]
    assert analyze(txns).months_covered == 4               # Jan..Apr inclusive


# ---------------------------------------------------------------------------
# Reconciliation: bank vs bureau obligations (pure)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bank,conf,bureau,status,flagged", [
    (None,  None, 12000, "no_bank_statement", False),
    (12000, 0.9,  12000, "agree",             False),
    (13000, 0.9,  12000, "agree",             False),   # within 15%
    (30000, 0.9,  12000, "bank_higher",       True),    # hidden debt
    (5000,  0.9,  12000, "bank_lower",        False),   # informational
    (30000, 0.3,  12000, "low_confidence",    False),   # too weak to act on
    (8000,  0.9,  0,     "bank_higher",       True),    # bureau missed it entirely
])
def test_reconcile_obligations_cases(bank, conf, bureau, status, flagged):
    r = reconcile_obligations(bank, conf, bureau)
    assert r["status"] == status
    assert (r["flag"] == "OBLIGATIONS_UNDERREPORTED_BY_BUREAU") == flagged


def test_reconcile_reads_policy_threshold_by_default(monkeypatch):
    # Within default 15% → agree. Tighten the policy to 5% → now a discrepancy.
    assert reconcile_obligations(13000, 0.9, 12000)["status"] == "agree"
    patched = {**policy.CASHFLOW_POLICY, "v1": {**policy.CASHFLOW_POLICY["v1"],
                                                "obligations_tol_pct": 0.05, "obligations_min_delta": 100}}
    monkeypatch.setattr("lending.agents.underwriting.CASHFLOW_POLICY", patched)
    assert reconcile_obligations(13000, 0.9, 12000)["status"] == "bank_higher"


# ---------------------------------------------------------------------------
# REFER routing in the decision engine
# ---------------------------------------------------------------------------

_CLEAN = ApplicantFeatures(age=35, monthly_income=80000, monthly_obligations=8000, cibil_score=780,
                           employment_tenure_months=48, loan_amount_requested=300000,
                           loan_tenure_months=36, is_salaried=True, has_cibil_record=True)
_LOW_CIBIL = ApplicantFeatures(**{**{k: getattr(_CLEAN, k) for k in _CLEAN.__dataclass_fields__},
                                  "cibil_score": 600})


def test_decide_approves_clean_without_flag():
    assert decide(_CLEAN).disposition.value == "approve"


def test_cashflow_flag_refers_an_approvable_application():
    d = decide(_CLEAN, cashflow_flags=["OBLIGATIONS_UNDERREPORTED_BY_BUREAU"])
    assert d.disposition.value == "refer"
    assert "CASHFLOW_OBLIGATIONS_DISCREPANCY" in d.reason_codes
    assert verify_faithful(d.reason_codes, d.explanation)   # explanation covers exactly the codes


def test_cashflow_flag_never_overrides_a_decline():
    d = decide(_LOW_CIBIL, cashflow_flags=["OBLIGATIONS_UNDERREPORTED_BY_BUREAU"])
    assert d.disposition.value == "decline"
    assert "CASHFLOW_OBLIGATIONS_DISCREPANCY" not in d.reason_codes


# ---------------------------------------------------------------------------
# Income cross-check through Document Intelligence
# ---------------------------------------------------------------------------

def _rec(value, conf=0.96):
    return {"value": value, "ocr_conf": conf}


def _income_extractions(bank_net):
    return {
        "salary_slips": {"name": _rec("Aarav Sharma"), "gross_monthly_income": _rec(60000),
                         "net_monthly_income": _rec(49000)},
        "bank_statement": {"net_monthly_income": _rec(bank_net, 1.0)},
    }


def test_bank_income_agreeing_with_payslip_is_reliable():
    res = evaluate(_income_extractions(51000))             # within 10%
    fc = res.field_confidence["net_monthly_income"]
    assert fc.is_reliable
    assert all(c.matches for c in res.cross_checks if c.field_name == "net_monthly_income")


def test_bank_income_mismatch_flags_and_lowers_confidence():
    res = evaluate(_income_extractions(30000))             # >10% below payslip
    fc = res.field_confidence["net_monthly_income"]
    assert not fc.is_reliable
    assert "CROSS_SOURCE_MISMATCH" in [f.value for f in fc.risk_flags]
    # net_monthly_income is NOT a KYC key field → it must not gate by itself.
    assert not any(r.startswith("low_confidence:net_monthly_income")
                   or "net_monthly_income" in r for r in res.exception_reasons)


# ---------------------------------------------------------------------------
# Onboarding: bank_statement is a required document
# ---------------------------------------------------------------------------

def _repo():
    return ApplicationRepository(make_engine())


def test_bank_statement_is_a_required_document():
    assert "bank_statement" in REQUIRED_DOCUMENTS


def test_bank_statement_gates_completeness_until_uploaded():
    repo = _repo()
    app = Application(applicant=Applicant(full_name="Aarav Sharma"), features={})
    repo.save(app)
    # Required but not yet uploaded → it is a missing item.
    assert "document:bank_statement" in missing_fields(repo.get(app.application_id))
    register_document(repo, app.application_id, "bank_statement")
    app = repo.get(app.application_id)
    assert app.features["documents"]["bank_statement"]["uploaded"] is True
    assert "document:bank_statement" not in missing_fields(app)


def test_unknown_document_type_still_rejected():
    repo = _repo()
    app = Application(applicant=Applicant(full_name="X"), features={})
    repo.save(app)
    with pytest.raises(ValueError, match="unknown document type"):
        register_document(repo, app.application_id, "passport")
