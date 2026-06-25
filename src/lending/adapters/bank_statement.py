"""
Bank-statement extractor (#53, Phase 1) — turn a bank statement into the two
cross-validation features the cashflow core derives, in the same
`{field: {value, ocr_conf}}` shape the Document Intelligence agent (#19) already
consumes.

Unlike `llm_ocr.py` (which reads a field off a page), a statement is a transaction
time-series. So the split from §2.1 moves down a level: the LLM **labels** each
transaction (direction + category from a closed set, with a verbatim
`source_quote`); deterministic code in `lending.cashflow` does every sum and
average. The model never reports an income or obligations figure.

Confidence is grounded from three observable signals, none self-reported:
  • self-consistency — the statement is extracted N times (temperature > 0); a
    feature's value is the median across samples and confidence scales with how
    many samples agree on it.
  • provenance — each transaction carries a verbatim `source_quote`; the fraction
    of a feature's contributing lines whose quote is actually present in the
    statement text grounds against hallucinated transactions.
  • recurrence regularity — intrinsic to the statement (a salary lands every
    month; an EMI recurs), computed inside `cashflow.analysis`.
final ocr_conf = self_consistency × provenance × recurrence_regularity.

Everything is injectable: `make_bank_statement_extractor(load_document,
statement_pass)` takes a document loader and a single-pass extractor, so tests run
with fakes and no API key. `gemini_statement_pass()` is the live pass;
`python -m lending.adapters.bank_statement <file> [samples]` runs it on a file.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from statistics import median
from typing import Callable, Optional

from pydantic import BaseModel

from lending.cashflow import (
    DerivedFeature,
    Transaction,
    TxnCategory,
    TxnDirection,
    analyze,
)

# Reuse the document loading + provenance helpers from the OCR extractor rather
# than duplicate them — a statement is loaded exactly like any other document.
from .llm_ocr import Document, _quote_present, call_with_retry, load_file, make_store_loader, pdf_text

# This adapter produces these canonical fields. `net_monthly_income` overlaps the
# salary slip / Form-16, so Document Intelligence cross-checks it for free;
# `monthly_obligations` is single-source at the KYC stage (cross-checked against
# the bureau later, at underwriting — Step 4).
BANK_STATEMENT_DOC_TYPE = "bank_statement"

# A single LLM pass over one statement → a list of raw transaction dicts
# {date, description, amount, direction, category, source_quote}.
StatementPass = Callable[[Document], list[dict]]
# Loads the stored statement for (application_id, doc_type).
LoadDocument = Callable[[str, str], Document]


# ---------------------------------------------------------------------------
# Parsing / normalization (pure) — raw LLM dicts → validated Transactions
# ---------------------------------------------------------------------------

def _to_float(value) -> Optional[float]:
    digits = re.sub(r"[^0-9.]", "", str(value))
    try:
        return abs(float(digits)) if digits else None
    except ValueError:
        return None


_MONTH_NUM = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _norm_date(value) -> Optional[str]:
    """Best-effort ISO (YYYY-MM-DD) normalization. The analysis only keys off the
    year-month, so day precision is non-critical — but recovering the month is
    essential. Handles three real-statement shapes:
      • ISO            2026-04-01
      • all-numeric    01/04/2026, 01-04-26  (DD before MM, the Indian default)
      • month-name     01 Apr 2026, 01-APR-26, Apr 1 2026
    Returns None only when no month can be recovered (a dateless line is unusable)."""
    s = str(value).strip().lower()
    digits = [int(d) for d in re.findall(r"\d+", s)]

    # Month-name form: find the month token, recover year + day from the numbers.
    month_word = next((w for w in re.findall(r"[a-z]+", s) if w in _MONTH_NUM), None)
    if month_word and digits:
        month = _MONTH_NUM[month_word]
        year = next((n for n in digits if n > 31), None)
        if year is None and digits:                       # 2-digit year, take the last number
            year = digits[-1]
        year = year + 2000 if year is not None and year < 100 else year
        day = next((n for n in digits if n != year and 1 <= n <= 31), 1)
        return f"{year:04d}-{month:02d}-{day:02d}" if year else None

    # All-numeric forms.
    if len(digits) >= 3:
        a, b, c = digits[0], digits[1], digits[2]
        if a > 1000:                                      # YYYY-MM-DD
            return f"{a:04d}-{b:02d}-{c:02d}"
        year = c + 2000 if c < 100 else c                 # DD-MM-YYYY (or 2-digit year)
        return f"{year:04d}-{b:02d}-{a:02d}"
    return None


# Bank statements label direction many ways; normalize the common ones rather than
# drop a line we can't map. Sign on the amount is handled separately by the caller.
_DIRECTION_ALIASES = {
    "CREDIT": TxnDirection.CREDIT, "CREDITED": TxnDirection.CREDIT, "CR": TxnDirection.CREDIT,
    "C": TxnDirection.CREDIT, "DEPOSIT": TxnDirection.CREDIT, "IN": TxnDirection.CREDIT,
    "DEBIT": TxnDirection.DEBIT, "DEBITED": TxnDirection.DEBIT, "DR": TxnDirection.DEBIT,
    "D": TxnDirection.DEBIT, "WITHDRAWAL": TxnDirection.DEBIT, "OUT": TxnDirection.DEBIT,
}


def _norm_direction(value) -> Optional[TxnDirection]:
    return _DIRECTION_ALIASES.get(str(value).strip().upper())


def _to_transactions(raw: list[dict]) -> list[Transaction]:
    """Coerce one sample's raw transaction dicts into validated Transactions.

    A line is dropped (not guessed) when it lacks a parseable date/amount or a
    recognized direction — trusting a malformed line would let model noise into the
    arithmetic. An unrecognized category degrades to OTHER (it just won't feed a
    feature), which is safer than dropping a real transaction."""
    out: list[Transaction] = []
    for r in raw or []:
        date = _norm_date(r.get("date"))
        amount = _to_float(r.get("amount"))
        direction = _norm_direction(r.get("direction"))
        if date is None or amount is None or direction is None:
            continue
        try:
            category = TxnCategory(str(r.get("category", "")).strip().upper())
        except ValueError:
            category = TxnCategory.OTHER
        out.append(Transaction(
            date=date,
            description=str(r.get("description", "")).strip(),
            amount=amount,
            direction=direction,
            category=category,
            source_quote=str(r.get("source_quote", "")),
        ))
    return out


def _provenance(transactions: list[Transaction], doc_text: Optional[str]) -> float:
    """Fraction of these transactions whose `source_quote` is actually present in
    the statement text. No text layer (scanned PDF / image) → provenance can't be
    verified, so it's neutral (1.0) and confidence rests on consistency +
    regularity. No transactions → neutral."""
    if not doc_text or not transactions:
        return 1.0
    present = sum(1 for t in transactions if _quote_present(t.source_quote, doc_text))
    return present / len(transactions)


# ---------------------------------------------------------------------------
# Grounding (pure) — N sampled extractions → canonical {field: {value, ocr_conf}}
# ---------------------------------------------------------------------------

def _aggregate(features: list[DerivedFeature], *, tol_pct: float) -> Optional[DerivedFeature]:
    """Combine the same DerivedFeature across N samples into one. Value = median;
    self-consistency = fraction of samples within `tol_pct` of that median; final
    confidence = self_consistency × median(per-sample confidence) — so it folds the
    samples' own provenance×regularity together with cross-sample agreement. Basis
    and flags are taken from the sample closest to the median."""
    if not features:
        return None
    values = [f.value for f in features]
    med = median(values)
    if med == 0:
        consistency = sum(1 for v in values if v == 0) / len(values)
    else:
        consistency = sum(1 for v in values if abs(v - med) / med <= tol_pct) / len(values)
    base_conf = median(f.confidence for f in features)
    rep = min(features, key=lambda f: abs(f.value - med))
    return DerivedFeature(
        value=round(med, 2),
        confidence=round(consistency * base_conf, 4),
        months_observed=rep.months_observed,
        basis=rep.basis,
        flags=rep.flags,
    )


def _feature_record(feat: DerivedFeature) -> dict:
    """A DerivedFeature → the canonical extraction record (value + ocr_conf) plus
    cashflow audit detail (ignored by Document Intelligence, kept for the audit log
    / underwriter review)."""
    return {
        "value": feat.value,
        "ocr_conf": feat.confidence,
        "months_observed": feat.months_observed,
        "flags": list(feat.flags),
        "basis": list(feat.basis),
        "source_quote": "",  # derived, not read from a single line; provenance already folded in
    }


def ground_cashflow(
    samples: list[list[dict]],
    doc_text: Optional[str] = None,
    *,
    amount_tol_pct: float = 0.05,
    min_recurrence_months: int = 2,
    consistency_tol_pct: float = 0.05,
) -> dict:
    """Combine N sampled statement extractions into canonical
    `{net_monthly_income: {...}, monthly_obligations: {...}}`. Each sample is parsed,
    grounded for provenance, and analysed independently; the per-feature results are
    then aggregated across samples. Matches the ExtractFn contract: a feature never
    observed is omitted (income), while a confidently-zero obligations figure is
    kept."""
    if not samples:
        return {}

    incomes: list[DerivedFeature] = []
    obligations: list[DerivedFeature] = []
    for raw in samples:
        txns = _to_transactions(raw)
        prov_income = _provenance([t for t in txns if t.category is TxnCategory.SALARY], doc_text)
        prov_oblig = _provenance([t for t in txns if t.category is TxnCategory.LOAN_EMI], doc_text)
        result = analyze(
            txns,
            income_provenance=prov_income,
            obligations_provenance=prov_oblig,
            amount_tol_pct=amount_tol_pct,
            min_recurrence_months=min_recurrence_months,
        )
        if result.net_monthly_income is not None:
            incomes.append(result.net_monthly_income)
        if result.monthly_obligations is not None:
            obligations.append(result.monthly_obligations)

    out: dict = {}
    # `net_monthly_income` keeps the shared canonical name on purpose: Document
    # Intelligence then cross-checks it against the payslip's net income for free.
    income = _aggregate(incomes, tol_pct=consistency_tol_pct)
    if income is not None:
        out["net_monthly_income"] = _feature_record(income)
    # Obligations are namespaced under `bank_monthly_obligations` so they DON'T
    # collide with the engine's `monthly_obligations` (which is bureau-sourced and
    # the binding DTI input). This bank figure is the cross-check, reconciled
    # against the bureau at underwriting (#53 Phase 1) — it never overwrites DTI.
    obligation = _aggregate(obligations, tol_pct=consistency_tol_pct)
    if obligation is not None:
        out["bank_monthly_obligations"] = _feature_record(obligation)
    return out


# ---------------------------------------------------------------------------
# Extractor factory
# ---------------------------------------------------------------------------

def make_bank_statement_extractor(
    load_document: LoadDocument,
    statement_pass: StatementPass,
    *,
    samples: int = 3,
    amount_tol_pct: float = 0.05,
    min_recurrence_months: int = 2,
    consistency_tol_pct: float = 0.05,
    on_progress: Optional[Callable[[str], None]] = None,
):
    """Build an ExtractFn for the bank statement: load it, sample the LLM `samples`
    times, ground + analyse, and return canonical cashflow features. Returns {} for
    any other doc_type, so it composes with the OCR extractor (route by doc_type).
    `load_document` and `statement_pass` are injected (fakes in tests, live impls in
    prod)."""

    def emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def extract(application_id: str, doc_type: str) -> dict:
        if doc_type != BANK_STATEMENT_DOC_TYPE:
            return {}
        emit(f"[{doc_type}] loading statement…")
        document = load_document(application_id, doc_type)
        emit(f"[{doc_type}] {document.mime_type}, text layer: "
             f"{'yes' if getattr(document, 'text', None) else 'no (provenance unverified)'}")
        import time

        runs: list[list[dict]] = []
        for i in range(samples):
            emit(f"[{doc_type}] sample {i + 1}/{samples}: calling model…")
            t0 = time.monotonic()
            run = statement_pass(document)
            dt = time.monotonic() - t0
            runs.append(run)
            # Report raw vs. parsed: a large gap means many lines were dropped
            # (unparseable date/amount/direction) and the features will be thin —
            # far better to surface that than to silently return {}.
            parsed = len(_to_transactions(run))
            emit(f"[{doc_type}] sample {i + 1}/{samples}: {len(run)} transactions, "
                 f"{parsed} parsed  ({dt:.1f}s)")

        result = ground_cashflow(
            runs, getattr(document, "text", None),
            amount_tol_pct=amount_tol_pct, min_recurrence_months=min_recurrence_months,
            consistency_tol_pct=consistency_tol_pct,
        )
        for field, rec in result.items():
            emit(f"[{doc_type}]   ⮑ {field} = {rec['value']!r}  (conf {rec['ocr_conf']})")
        return result

    return extract


# ---------------------------------------------------------------------------
# Live Gemini statement pass (lazy imports — no SDK/key needed to import)
# ---------------------------------------------------------------------------

class _Txn(BaseModel):
    date: str
    description: str
    amount: float
    direction: str            # CREDIT | DEBIT
    category: str             # one of TxnCategory
    source_quote: str         # verbatim statement line the value was read from


class _Statement(BaseModel):
    transactions: list[_Txn]


_CATEGORIES = ", ".join(c.value for c in TxnCategory)
_PROMPT = (
    "You are reading an Indian bank account statement. Transcribe EVERY transaction "
    "line you can see. For each transaction return:\n"
    "  - date (the transaction date),\n"
    "  - description (the narration / particulars, verbatim),\n"
    "  - amount (digits only, no sign, no currency symbol or commas),\n"
    "  - direction: 'CREDIT' if money came in, 'DEBIT' if money went out,\n"
    f"  - category: exactly one of [{_CATEGORIES}]. Use SALARY only for regular "
    "employment income credits; use LOAN_EMI only for loan / credit-card / BNPL "
    "instalment debits. When unsure, use OTHER.\n"
    "  - source_quote: the transaction's text copied VERBATIM from the statement.\n"
    "Do NOT invent transactions or infer ones that are not visibly present. Omit a "
    "line rather than fabricate it."
)


def gemini_statement_pass(*, model: Optional[str] = None, temperature: float = 0.4,
                          retries: int = 3, backoff_s: float = 1.5) -> StatementPass:
    """A live single-pass statement extractor backed by Gemini (multimodal).
    Temperature > 0 so repeated samples vary, which is what self-consistency
    measures. Transient connection failures are retried with backoff."""
    from lending.agents.llm import model_pro

    chosen = model or model_pro()

    def statement_pass(document: Document) -> list[dict]:
        import sys

        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")

        client = genai.Client(api_key=api_key)
        print(f"      · payload: {len(document.data) / 1024:.0f} KB {document.mime_type}, model={chosen}",
              file=sys.stderr, flush=True)

        def _call():
            return client.models.generate_content(
                model=chosen,
                contents=[
                    types.Part.from_bytes(data=document.data, mime_type=document.mime_type),
                    _PROMPT,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_Statement,
                    temperature=temperature,
                ),
            )

        response = call_with_retry(
            _call, retries=retries, backoff_s=backoff_s,
            on_retry=lambda a, n, err, delay: print(
                f"      · attempt {a}/{n} failed ({type(err).__name__}: {err}); "
                f"retrying in {delay:.1f}s…", file=sys.stderr, flush=True),
        )

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, _Statement):
            items = [t.model_dump() for t in parsed.transactions]
        else:
            items = json.loads(response.text).get("transactions", [])
        return items

    return statement_pass


def _main() -> None:  # python -m lending.adapters.bank_statement <file> [samples] [model]
    import sys

    from lending.agents.llm import model_lite, model_pro

    from .llm_ocr import _load_dotenv
    _load_dotenv()

    if len(sys.argv) < 2:
        print("usage: python -m lending.adapters.bank_statement <file> [samples] [lite|pro|<model-id>]")
        raise SystemExit(2)

    path = sys.argv[1]
    log = lambda msg: print(msg, file=sys.stderr, flush=True)

    # Offline re-ground: if given a previously-saved <file>.raw.json, skip the API
    # entirely and just re-run parsing + grounding. Lets us iterate on the pure
    # logic for free instead of paying 3×~60s of model calls each time.
    if path.endswith(".raw.json"):
        saved = json.load(open(path))
        runs, doc_text = saved["runs"], saved.get("doc_text")
        for i, run in enumerate(runs):
            log(f"[reground] sample {i + 1}/{len(runs)}: {len(run)} transactions, "
                f"{len(_to_transactions(run))} parsed")
        result = ground_cashflow(runs, doc_text)
        log("─" * 40)
        print(json.dumps(result, indent=2, default=str))
        return

    samples = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    # Default to the lite model: a statement is many transactions, but N-sample
    # self-consistency + provenance grounding compensates for a weaker per-call
    # model, and lite is far faster/cheaper for the repeated calls. Pass "pro" to
    # compare against the heavier model.
    model_arg = sys.argv[3] if len(sys.argv) > 3 else "lite"
    model = {"lite": model_lite(), "pro": model_pro()}.get(model_arg, model_arg)

    document = load_file(path)
    log(f"→ analysing statement {path} with {samples} samples (model={model})")
    log(f"  {len(document.data) / 1024:.0f} KB ({document.mime_type}), "
        f"text layer: {'yes' if document.text else 'no'}")

    # Call the model directly so we can save the raw transactions before grounding.
    statement_pass = gemini_statement_pass(model=model)
    import time
    runs: list[list[dict]] = []
    for i in range(samples):
        log(f"sample {i + 1}/{samples}: calling model…")
        t0 = time.monotonic()
        run = statement_pass(document)
        log(f"sample {i + 1}/{samples}: {len(run)} transactions, "
            f"{len(_to_transactions(run))} parsed  ({time.monotonic() - t0:.1f}s)")
        runs.append(run)

    raw_path = path + ".raw.json"
    json.dump({"runs": runs, "doc_text": document.text}, open(raw_path, "w"), default=str)
    log(f"raw samples saved → {raw_path}  (reground offline: "
        f"python -m lending.adapters.bank_statement {raw_path})")

    result = ground_cashflow(runs, document.text)
    log("─" * 40)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _main()
