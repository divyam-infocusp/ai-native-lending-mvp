"""
Cashflow domain models (#53, Phase 1) — the structured output of bank-statement
analysis.

A bank statement is not a field-extraction problem (like an Aadhaar card) but a
transaction time-series problem, so the §2.1 discipline ("LLM perceives, code
decides") holds with the split moved one level down: an LLM *labels* each
transaction (is this credit a salary? is this debit a loan EMI?), but every
NUMBER that can reach the credit decision — monthly income, monthly obligations —
is computed by deterministic code in `cashflow/analysis.py`, never reported by the
model.

Phase 1 scope is **cross-validation only**: the two derived features below
corroborate signals the pipeline already has (payslip/claimed income at the KYC
stage; bureau obligations at underwriting). Using them as standalone underwriting
inputs — and feeding obligations into DTI — is a later, risk-signed-off step.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TxnDirection(str, Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class TxnCategory(str, Enum):
    """The label an extractor assigns to a transaction. A small, closed set so the
    LLM classifies into known buckets and deterministic code can reason over them.
    Only SALARY (→ income) and LOAN_EMI (→ obligations) drive Phase 1 features; the
    rest exist so the model has somewhere honest to put everything else rather than
    forcing a misclassification."""
    # --- credits ---
    SALARY = "SALARY"               # regular employment income
    OTHER_INCOME = "OTHER_INCOME"   # interest, dividends, irregular/one-off income
    TRANSFER_IN = "TRANSFER_IN"     # P2P / self transfers received
    REVERSAL = "REVERSAL"           # refunds, failed-transaction reversals
    # --- debits ---
    LOAN_EMI = "LOAN_EMI"           # loan / card / BNPL EMI outflow (an obligation)
    RENT = "RENT"
    UTILITIES = "UTILITIES"
    TRANSFER_OUT = "TRANSFER_OUT"   # P2P / self transfers sent
    DISCRETIONARY = "DISCRETIONARY" # shopping, food, entertainment, etc.
    OTHER = "OTHER"


@dataclass(frozen=True)
class Transaction:
    """One labeled statement line. `amount` is a positive magnitude; `direction`
    carries the sign. `source_quote` is the verbatim statement text the line was
    read from — its presence in the document is what grounds confidence (provenance),
    so it is never optional in the live path even though it defaults to empty for
    hand-built test fixtures."""
    date: str                   # ISO date, YYYY-MM-DD (normalized by the extractor)
    description: str
    amount: float               # positive magnitude, INR
    direction: TxnDirection
    category: TxnCategory
    source_quote: str = ""      # verbatim statement text → provenance grounding


@dataclass(frozen=True)
class DerivedFeature:
    """A single deterministically-computed cashflow figure plus its grounded
    confidence and the basis it was computed from (kept for audit / underwriter
    review — a referred decision must be explainable)."""
    value: float                # INR per month
    confidence: float           # grounded composite [0, 1] — never LLM self-report
    months_observed: int        # months this feature actually appeared in
    basis: tuple = ()           # contributing detail (immutable), for audit
    flags: tuple = ()           # diagnostic strings (e.g. "IRREGULAR_SALARY")


@dataclass(frozen=True)
class CashflowAnalysis:
    """The full result of analysing one bank statement: the statement's span, the
    two Phase-1 cross-validation features (each may be None if not observed), and
    the labeled transactions for audit."""
    months_covered: int
    net_monthly_income: DerivedFeature | None
    monthly_obligations: DerivedFeature | None
    transactions: tuple = ()
