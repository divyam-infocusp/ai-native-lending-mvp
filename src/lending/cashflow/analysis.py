"""
Cashflow analysis (#53, Phase 1) — deterministic aggregation of labeled bank-
statement transactions into corroborating features.

"LLM perceives, code decides" (§2.1): the category on each Transaction comes from
a model; everything in THIS module is pure arithmetic over those labels. No value
here is taken from a model's self-report. Grounding follows the Confidence Service
spirit (§16.4) — a feature's confidence is built from observable signals, not an
opinion:

  • regularity — a salary should land every month the statement spans; an EMI
    stream should recur. A figure seen in 3 of 3 months is trusted more than one
    seen in 1 of 3.
  • provenance — the fraction of contributing transactions whose verbatim
    `source_quote` was actually found in the statement text. Computed by the
    extractor (which holds the document text) and injected here, so this module
    stays pure and unit-testable without any document or LLM.

Two features, both for cross-validation only in Phase 1:
  • net_monthly_income  — mean of monthly SALARY credits → corroborates the
    payslip's net income and the applicant's claimed income (KYC stage).
  • monthly_obligations — per-month total of *recurring* LOAN_EMI debit streams →
    corroborates the bureau's obligations and surfaces informal/BNPL debits a
    bureau pull misses (underwriting stage). It does NOT feed DTI in Phase 1.

Thresholds (recurrence tolerance, minimum recurrence) are parameters with
placeholder defaults here; they move to a versioned CASHFLOW_POLICY when this core
is wired in (Step 5), mirroring how every other engine reads `policy.py`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from statistics import mean, median

from .models import CashflowAnalysis, DerivedFeature, Transaction, TxnCategory, TxnDirection


def _year_month(iso_date: str) -> str:
    """'2026-03-14' → '2026-03'. Assumes the extractor normalized dates to ISO."""
    return str(iso_date)[:7]


def _month_index(year_month: str) -> int:
    """'2026-03' → absolute month count, so a contiguous range is just a range of
    ints. Tolerant of a bad value (returns -1) so one unparseable date can't crash
    the analysis."""
    try:
        y, m = year_month.split("-")
        return int(y) * 12 + (int(m) - 1)
    except (ValueError, AttributeError):
        return -1


def months_covered(transactions) -> list[str]:
    """The *inclusive* calendar span between the earliest and latest transaction —
    not just the months that happen to have activity. A salary- or EMI-less month
    inside the span is still a covered month, so a gap correctly lowers a feature's
    regularity (and flags IRREGULAR_SALARY) instead of being silently invisible."""
    idxs = sorted(i for i in (_month_index(_year_month(t.date)) for t in transactions if t.date) if i >= 0)
    if not idxs:
        return []
    return [f"{i // 12:04d}-{i % 12 + 1:02d}" for i in range(idxs[0], idxs[-1] + 1)]


# ---------------------------------------------------------------------------
# Net monthly income — mean of monthly SALARY credits
# ---------------------------------------------------------------------------

def net_monthly_income(transactions, *, provenance: float = 1.0) -> DerivedFeature | None:
    """Mean monthly SALARY credit across the months the statement covers.

    Returns None when no salary credit is observed at all (no signal — distinct
    from a confident zero). Confidence = salary-month regularity × provenance:
    salary seen in every covered month scores full regularity; a gap lowers it and
    raises IRREGULAR_SALARY."""
    months = months_covered(transactions)
    if not months:
        return None

    salary_by_month: dict[str, float] = defaultdict(float)
    for t in transactions:
        if t.direction is TxnDirection.CREDIT and t.category is TxnCategory.SALARY:
            salary_by_month[_year_month(t.date)] += t.amount
    if not salary_by_month:
        return None

    value = mean(salary_by_month.values())
    regularity = len(salary_by_month) / len(months)
    flags = ["IRREGULAR_SALARY"] if len(salary_by_month) < len(months) else []
    return DerivedFeature(
        value=round(value, 2),
        confidence=round(regularity * provenance, 4),
        months_observed=len(salary_by_month),
        basis=tuple(sorted(salary_by_month.items())),
        flags=tuple(flags),
    )


# ---------------------------------------------------------------------------
# Monthly obligations — per-month total of *recurring* LOAN_EMI debit streams
# ---------------------------------------------------------------------------

_MONTH_TOKENS = frozenset({
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
})


def _norm_payee(description: str) -> str:
    """Collapse a transaction narration to a comparable payee key — its stable
    alphabetic core with the parts that vary month to month removed. Real NACH/ACH
    EMI narrations differ each month in their digits (reference numbers, sequence
    counters) and embedded date, e.g. 'ACH/HDFC/HOMELOAN/0012345/12MAR' and
    '.../0019988/14APR'. Tokenizing on letters drops the digits, and dropping month
    tokens drops the embedded date — both collapse to 'achhdfchomeloan' so the same
    obligation groups across months. Amount tolerance (see `_group_streams`) keeps
    genuinely different loans with similar text apart."""
    tokens = re.findall(r"[a-z]+", str(description).lower())
    return "".join(t for t in tokens if t not in _MONTH_TOKENS)


def _group_streams(emis: list, *, amount_tol_pct: float) -> list[list]:
    """Greedily group EMI debits into streams that look like the *same* obligation:
    same normalized payee AND amount within `amount_tol_pct` of the stream's first
    member. A genuine EMI is a fixed recurring debit, so this is deliberately
    strict — two unrelated one-off debits won't merge into a phantom obligation."""
    streams: list[list] = []
    reps: list[tuple] = []  # (payee_key, representative_amount) per stream
    for t in emis:
        key = _norm_payee(t.description)
        placed = False
        for i, (rep_key, rep_amt) in enumerate(reps):
            if rep_key == key and rep_amt > 0 and abs(t.amount - rep_amt) / rep_amt <= amount_tol_pct:
                streams[i].append(t)
                placed = True
                break
        if not placed:
            streams.append([t])
            reps.append((key, t.amount))
    return streams


def monthly_obligations(
    transactions,
    *,
    provenance: float = 1.0,
    amount_tol_pct: float = 0.05,
    min_recurrence_months: int = 2,
) -> DerivedFeature | None:
    """Per-month total of recurring loan/EMI obligations visible on the statement.

    Only EMI streams that recur in at least `min_recurrence_months` distinct months
    count — a one-off debit is not a fixed obligation. Each qualifying stream
    contributes the median of its amounts (robust to an outlier month). Confidence
    = mean stream recurrence × provenance.

    Returns a confident **zero** when EMI debits exist but none recur, or none are
    seen at all — "no bank-visible obligations" is itself a usable signal. Returns
    None only when the statement has no transactions to reason about."""
    months = months_covered(transactions)
    if not months:
        return None

    emis = [
        t for t in transactions
        if t.direction is TxnDirection.DEBIT and t.category is TxnCategory.LOAN_EMI
    ]
    if not emis:
        return DerivedFeature(value=0.0, confidence=round(provenance, 4),
                              months_observed=0, basis=(), flags=())

    streams = _group_streams(emis, amount_tol_pct=amount_tol_pct)
    recurring = [
        s for s in streams
        if len({_year_month(t.date) for t in s}) >= min_recurrence_months
    ]
    if not recurring:
        return DerivedFeature(value=0.0, confidence=round(provenance, 4),
                              months_observed=0, basis=("no_recurring_emi",), flags=())

    monthly_total = sum(median(t.amount for t in s) for s in recurring)
    recurrence = mean(
        len({_year_month(t.date) for t in s}) / len(months) for s in recurring
    )
    basis = tuple(
        {
            "payee": _norm_payee(s[0].description),
            "monthly_amount": round(median(t.amount for t in s), 2),
            "months_seen": len({_year_month(t.date) for t in s}),
        }
        for s in recurring
    )
    return DerivedFeature(
        value=round(monthly_total, 2),
        confidence=round(recurrence * provenance, 4),
        months_observed=max(len({_year_month(t.date) for t in s}) for s in recurring),
        basis=basis,
        flags=(),
    )


# ---------------------------------------------------------------------------
# Top-level analysis
# ---------------------------------------------------------------------------

def analyze(
    transactions,
    *,
    income_provenance: float = 1.0,
    obligations_provenance: float = 1.0,
    amount_tol_pct: float = 0.05,
    min_recurrence_months: int = 2,
) -> CashflowAnalysis:
    """Run the full Phase-1 analysis over a list of labeled Transactions. Pure —
    callers (the extractor) compute provenance from the document text and pass it
    in. Provenance is split per feature so a hallucinated salary line doesn't
    depress confidence in well-grounded obligations, and vice versa."""
    txns = tuple(transactions)
    return CashflowAnalysis(
        months_covered=len(months_covered(txns)),
        net_monthly_income=net_monthly_income(txns, provenance=income_provenance),
        monthly_obligations=monthly_obligations(
            txns,
            provenance=obligations_provenance,
            amount_tol_pct=amount_tol_pct,
            min_recurrence_months=min_recurrence_months,
        ),
        transactions=txns,
    )
