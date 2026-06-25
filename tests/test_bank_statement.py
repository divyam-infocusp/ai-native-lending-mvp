"""
Tests for the bank-statement extractor adapter (#53 Phase 1).

Exercises the pure, LLM-free parts: date/direction normalization, transaction
coercion (dropping unparseable lines), self-consistency + provenance grounding,
and doc-type routing. The live Gemini pass is not called — a fake statement_pass
stands in, mirroring how llm_ocr is tested.
"""
import pytest

from lending.adapters.bank_statement import (
    BANK_STATEMENT_DOC_TYPE,
    _norm_date,
    _norm_direction,
    _to_transactions,
    ground_cashflow,
    make_bank_statement_extractor,
)
from lending.adapters.llm_ocr import Document
from lending.cashflow import TxnDirection


# ---------------------------------------------------------------------------
# Date + direction normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,iso", [
    ("2026-04-01", "2026-04-01"),
    ("01/04/2026", "2026-04-01"),
    ("01-04-26", "2026-04-01"),
    ("01 Apr 2026", "2026-04-01"),
    ("01-APR-26", "2026-04-01"),
    ("Apr 1, 2026", "2026-04-01"),
    ("1 April 2026", "2026-04-01"),
])
def test_norm_date_handles_common_statement_formats(raw, iso):
    assert _norm_date(raw) == iso


def test_norm_date_returns_none_when_no_month():
    assert _norm_date("not a date") is None


@pytest.mark.parametrize("raw,expected", [
    ("Cr", TxnDirection.CREDIT), ("CREDIT", TxnDirection.CREDIT), ("deposit", TxnDirection.CREDIT),
    ("Dr", TxnDirection.DEBIT), ("DEBIT", TxnDirection.DEBIT), ("withdrawal", TxnDirection.DEBIT),
    ("nonsense", None),
])
def test_norm_direction(raw, expected):
    assert _norm_direction(raw) == expected


# ---------------------------------------------------------------------------
# Transaction coercion — drop what can't be trusted, keep what can
# ---------------------------------------------------------------------------

def test_to_transactions_coerces_and_drops_bad_lines():
    raw = [
        {"date": "01 Apr 2026", "description": "SALARY", "amount": "71,500.00",
         "direction": "CR", "category": "SALARY", "source_quote": "q"},
        {"date": "bad", "description": "x", "amount": "100", "direction": "DR", "category": "OTHER"},   # bad date
        {"date": "02 Apr 2026", "description": "y", "amount": "abc", "direction": "DR", "category": "OTHER"},  # bad amount
        {"date": "03 Apr 2026", "description": "z", "amount": "50", "direction": "??", "category": "OTHER"},   # bad direction
        {"date": "04 Apr 2026", "description": "w", "amount": "900", "direction": "DR", "category": "WHO_KNOWS"},  # unknown cat → OTHER
    ]
    txns = _to_transactions(raw)
    assert len(txns) == 2                                   # only the salary + the unknown-category line survive
    assert txns[0].amount == 71500.0 and txns[0].direction is TxnDirection.CREDIT
    assert txns[1].category.value == "OTHER"


# ---------------------------------------------------------------------------
# Grounding — self-consistency, provenance, output shape
# ---------------------------------------------------------------------------

def _sample(salary=71500, emi=25000):
    out = []
    for m in ("01", "02", "03"):
        out += [
            {"date": f"2026-{m}-01", "description": f"SAL/ACME/{m}", "amount": salary,
             "direction": "CR", "category": "SALARY", "source_quote": f"SAL/ACME/{m}"},
            {"date": f"2026-{m}-05", "description": f"ACH/HDFC/HOMELOAN/{m}", "amount": emi,
             "direction": "DR", "category": "LOAN_EMI", "source_quote": f"ACH/HDFC/HOMELOAN/{m}"},
        ]
    return out


def test_ground_cashflow_output_keys_and_namespacing():
    s = _sample()
    doc_text = " ".join(t["source_quote"] for t in s)
    out = ground_cashflow([s, s, s], doc_text)
    # net income keeps the shared name (so DocIntel cross-checks it); obligations
    # are namespaced so they don't collide with the bureau-sourced engine field.
    assert set(out) == {"net_monthly_income", "bank_monthly_obligations"}
    assert out["net_monthly_income"]["value"] == 71500.0
    assert out["bank_monthly_obligations"]["value"] == 25000.0
    assert out["net_monthly_income"]["ocr_conf"] == 1.0     # identical samples + provenance


def test_ground_cashflow_self_consistency_lowers_confidence_on_disagreement():
    s, bad = _sample(), _sample(salary=20000)
    doc_text = " ".join(t["source_quote"] for t in s)
    out = ground_cashflow([s, s, bad], doc_text)
    assert out["net_monthly_income"]["value"] == 71500.0    # median of 71500,71500,20000
    assert out["net_monthly_income"]["ocr_conf"] == pytest.approx(2 / 3, abs=1e-3)


def test_ground_cashflow_provenance_zero_when_quotes_absent():
    s = _sample()
    out = ground_cashflow([s], "completely unrelated statement text")
    assert out["net_monthly_income"]["ocr_conf"] == 0.0     # no source_quote found → hallucination guard


def test_ground_cashflow_empty_samples():
    assert ground_cashflow([]) == {}


# ---------------------------------------------------------------------------
# Extractor factory — doc-type routing
# ---------------------------------------------------------------------------

def test_extractor_routes_by_doc_type():
    s = _sample()
    doc = Document(data=b"x", mime_type="application/pdf",
                   text=" ".join(t["source_quote"] for t in s))
    ext = make_bank_statement_extractor(lambda a, d: doc, lambda document: s, samples=2)
    assert ext("app1", BANK_STATEMENT_DOC_TYPE)["net_monthly_income"]["value"] == 71500.0
    assert ext("app1", "pan_card") == {}                   # composes with the OCR extractor
