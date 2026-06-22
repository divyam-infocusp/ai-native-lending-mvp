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


# ---------------------------------------------------------------------------
# Reflective extractor — derives the "OCR" fields from the application's OWN
# data (the name / PAN / income the applicant actually provided), with valid-
# format fallbacks. This keeps the verified profile consistent with the real
# applicant (no fixed "Priya Sharma" for everyone) until the real OCR/KYC
# adapter (#9) lands.
# ---------------------------------------------------------------------------
import re

_FALLBACK_AADHAAR = "234567890124"   # passes Verhoeff
_FALLBACK_PAN = "ABCDE1234F"
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _valid_pan(value) -> str:
    return str(value).upper() if value and _PAN_RE.match(str(value).upper()) else _FALLBACK_PAN


def _valid_aadhaar(value) -> str:
    from lending.confidence import validate_aadhaar

    return str(value) if value and validate_aadhaar(str(value)).valid else _FALLBACK_AADHAAR


def make_reflective_ocr_extractor(repository):
    """Build an extractor that echoes the application's own data as the extracted
    fields (same values across documents, so cross-checks agree)."""

    def extract(application_id: str, doc_type: str) -> dict:
        app = repository.get(application_id)
        applicant = getattr(app, "applicant", None)
        feats = (getattr(app, "features", None) or {}) if app else {}

        name = (getattr(applicant, "full_name", None) or "Applicant")
        dob = (getattr(applicant, "date_of_birth", None) or "1994-02-11")
        pan = _valid_pan(getattr(applicant, "pan", None))
        aadhaar = _valid_aadhaar(getattr(applicant, "aadhaar", None))
        address = (getattr(applicant, "current_address", None) or "12 MG Road, Pune 411001")
        employer = feats.get("employer_name") or "Acme Corp"
        gross = float(feats.get("monthly_income") or feats.get("gross_monthly_income") or 90_000)
        net = round(gross * 0.8)

        per_doc = {
            "identity_proof": {"name": _rec(name), "date_of_birth": _rec(dob),
                               "aadhaar": _rec(aadhaar), "address": _rec(address)},
            "address_proof": {"name": _rec(name), "date_of_birth": _rec(dob), "pan": _rec(pan)},
            "salary_slips": {"name": _rec(name), "employer_name": _rec(employer),
                             "gross_monthly_income": _rec(gross), "net_monthly_income": _rec(net)},
            "bank_statement": {"name": _rec(name), "net_monthly_income": _rec(net)},
            "form16": {"name": _rec(name), "pan": _rec(pan), "employer_name": _rec(employer),
                       "gross_monthly_income": _rec(gross)},
        }
        return per_doc.get(doc_type, {})

    return extract
