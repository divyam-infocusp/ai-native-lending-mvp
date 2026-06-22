"""
Placeholder mock OCR/KYC extraction (pending #9).

The real OCR/KYC adapters (#9) will extract canonical fields from uploaded
documents. Until they exist, this mock returns a clean, internally-consistent
document set so the Document Intelligence Agent (#19) and the full origination
spine can be exercised end-to-end in the demo/`adapter_mode=mock` runtime.

Fixtures are keyed by purpose `extract:<doc_type>` (see make_ocr_extractor) and
carry the canonical {field: {value, ocr_conf}} shape the agent consumes. Income
is split into gross (slip/Form-16) vs net (slip/bank) per the #19 design.

NOTE: a mock adapter returns the SAME profile for every application — fine for a
demo, never for a pilot. The live path (`adapter_mode=live`) must use #9.
"""
from __future__ import annotations

from .mock import MockAdapter
from .registry import AdapterHarness

OCR_PROVIDER = "ocr"


def _rec(value, ocr: float = 0.97) -> dict:
    return {"value": value, "ocr_conf": ocr}


# A clean applicant whose fields agree across every document.
_CLEAN_FIXTURES: dict[str, dict] = {
    "extract:identity_proof": {   # Aadhaar
        "name": _rec("Priya Sharma"),
        "date_of_birth": _rec("1994-02-11"),
        "aadhaar": _rec("234567890124"),       # passes Verhoeff
        "address": _rec("12 MG Road, Pune 411001"),
    },
    "extract:address_proof": {    # PAN card
        "name": _rec("PRIYA SHARMA"),
        "date_of_birth": _rec("11/02/1994"),
        "pan": _rec("ABCDE1234F"),
    },
    "extract:salary_slips": {
        "name": _rec("Priya Sharma"),
        "employer_name": _rec("Acme Corp"),
        "gross_monthly_income": _rec(90_000),
        "net_monthly_income": _rec(72_000),
    },
    "extract:bank_statement": {
        "name": _rec("Priya Sharma"),
        "net_monthly_income": _rec(71_500),
    },
    "extract:form16": {
        "name": _rec("Priya Sharma"),
        "pan": _rec("abcde 1234 f"),
        "employer_name": _rec("Acme Corp"),
        "gross_monthly_income": _rec(91_000),
    },
}


def make_mock_ocr_harness(fixtures: dict | None = None) -> AdapterHarness:
    """An AdapterHarness with a single mock OCR adapter registered."""
    harness = AdapterHarness()
    harness.register(MockAdapter(OCR_PROVIDER, fixtures or _CLEAN_FIXTURES))
    return harness
