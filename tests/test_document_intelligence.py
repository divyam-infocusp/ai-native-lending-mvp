"""
Tests for the Document Intelligence Agent (#19).

Three layers, each tested in isolation:
  - doc_compare.values_match — type-aware field comparison (ids/names/money/dates)
  - build_cross_checks / evaluate — dynamic cross-source checks + grounded gating
  - verify_documents — the agent entry: persists profile + KYC, audits, routes

A valid Aadhaar (passes Verhoeff) is required wherever the format validator runs.
"""
import pytest

from lending.agents import (
    build_cross_checks,
    evaluate,
    make_ocr_extractor,
    score_profile,
    verify_documents,
)
from lending.agents.doc_compare import values_match
from lending.agents.document_intelligence import key_fields_for

KEY_FIELDS = key_fields_for("v1")
from lending.audit import AuditStore
from lending.confidence import RiskFlag
from lending.los import Applicant, Application, ApplicationRepository, KycStatus, make_engine

VALID_AADHAAR = "234567890124"   # passes Verhoeff
VALID_AADHAAR_2 = "998877665548"


def _rec(value, ocr=0.97):
    return {"value": value, "ocr_conf": ocr}


def _clean_extractions():
    """A consistent, high-quality document set for one applicant."""
    return {
        "aadhaar_card": {   # Aadhaar
            "name": _rec("Priya Sharma"),
            "date_of_birth": _rec("1994-02-11"),
            "aadhaar": _rec(VALID_AADHAAR),
            "address": _rec("12 MG Road, Pune 411001"),
        },
        "pan_card": {    # PAN card carrying name + dob + pan
            "name": _rec("PRIYA SHARMA"),
            "date_of_birth": _rec("11/02/1994"),       # different format, same date
            "pan": _rec("ABCDE1234F"),
        },
        "salary_slips": {
            "name": _rec("Priya Sharma"),
            "employer_name": _rec("Acme Corp"),
            "gross_monthly_income": _rec(90000),
            "net_monthly_income": _rec(72000),
        },
        "bank_statement": {
            "name": _rec("Priya Sharma"),
            "net_monthly_income": _rec(71500),         # ≈ net, within tolerance
        },
        "form16": {
            "name": _rec("Priya Sharma"),
            "pan": _rec("abcde 1234 f"),               # spacing noise, same PAN
            "employer_name": _rec("Acme Corp"),
            "gross_monthly_income": _rec(91000),       # annual/12, within tolerance
        },
    }


# ---------------------------------------------------------------------------
# Layer 1: type-aware comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,expected", [
    ("ABCDE1234F", "abcde 1234 f", True),     # id: normalize spaces + case
    ("ABCDE1234F", "ABCDE1234X", False),      # id: genuinely different
])
def test_id_comparison(a, b, expected):
    assert values_match("pan", a, b) is expected


@pytest.mark.parametrize("a,b,expected", [
    ("Priya Sharma", "SHARMA PRIYA", True),   # order-insensitive
    ("Priya Sharma", "Priya S", True),        # initial-tolerant
    ("Priya Sharma", "Rahul Verma", False),   # different person
])
def test_name_comparison(a, b, expected):
    assert values_match("name", a, b) is expected


@pytest.mark.parametrize("a,b,expected", [
    (90000, 91000, True),     # ~1% — bonuses/arrears slack
    (90000, 89500, True),     # within 10%
    (90000, 70000, False),    # 22% — too far
])
def test_money_comparison(a, b, expected):
    assert values_match("gross_monthly_income", a, b) is expected


@pytest.mark.parametrize("a,b,expected", [
    ("1994-02-11", "11/02/1994", True),       # ISO vs DD/MM/YYYY, same date
    ("1994-02-11", "1993-02-11", False),      # one digit off
])
def test_date_comparison(a, b, expected):
    assert values_match("date_of_birth", a, b) is expected


# ---------------------------------------------------------------------------
# Layer 2: dynamic cross-checks
# ---------------------------------------------------------------------------

def test_cross_checks_only_for_fields_with_two_or_more_sources():
    checks = build_cross_checks(_clean_extractions())
    by_field = {}
    for c in checks:
        by_field.setdefault(c.field_name, []).append(c)

    # name appears on all 5 docs → C(5,2) = 10 pairwise checks
    assert len(by_field["name"]) == 10
    # pan on 2 docs (pan_card, form16) → 1 check
    assert len(by_field["pan"]) == 1
    # gross_monthly_income on 2 docs (salary_slips, form16) → 1 check
    assert len(by_field["gross_monthly_income"]) == 1
    # single-source fields get NO check
    assert "aadhaar" not in by_field        # only aadhaar_card
    assert "address" not in by_field        # only aadhaar_card
    # all clean → every check matches, and both real sources are named
    assert all(c.matches for c in checks)
    assert all(c.source_a and c.source_b for c in checks)


def test_gross_and_net_are_never_cross_compared():
    """The income split must keep payslip-gross from being matched to bank-net."""
    checks = build_cross_checks(_clean_extractions())
    fields = {c.field_name for c in checks}
    # net is single-source-per-meaning here? net is on salary_slips + bank_statement → 1 check.
    net_checks = [c for c in checks if c.field_name == "net_monthly_income"]
    assert len(net_checks) == 1 and net_checks[0].matches  # 72000 vs 71500
    # there is never a check mixing the two semantic income fields
    assert "gross_monthly_income" in fields and "net_monthly_income" in fields


def test_key_fields_come_from_versioned_policy():
    from lending.policy import CONFIDENCE_POLICY
    assert key_fields_for("v1") == frozenset(CONFIDENCE_POLICY["v1"]["kyc_key_fields"])
    with pytest.raises(ValueError, match="Unknown policy_version"):
        key_fields_for("v999")


def test_metadata_fields_are_excluded():
    ext = {
        "salary_slips": {"name": _rec("Priya"), "period": _rec("May 2026")},
        "bank_statement": {"name": _rec("Priya"), "period": _rec("Apr 2026")},
    }
    checks = build_cross_checks(ext)
    assert {c.field_name for c in checks} == {"name"}   # period excluded despite 2 sources


# ---------------------------------------------------------------------------
# Layer 2: grounded gating (evaluate)
# ---------------------------------------------------------------------------

def test_clean_docs_verify_with_per_field_confidence():
    result = evaluate(_clean_extractions())
    assert result.status == "verified"
    assert result.exception_reasons == []
    for kf in KEY_FIELDS:
        assert result.field_confidence[kf].is_reliable is True
    assert result.profile["pan"] == "ABCDE1234F"   # chosen from the higher-OCR source


def test_cross_source_mismatch_on_key_field_routes_to_exception():
    ext = _clean_extractions()
    ext["pan_card"]["date_of_birth"] = _rec("1993-02-11")   # disagrees with Aadhaar
    result = evaluate(ext)
    assert result.status == "exception"
    assert any(r.startswith("cross_source_mismatch:date_of_birth") for r in result.exception_reasons)
    # the reason names both disagreeing sources
    assert any("aadhaar_card" in r and "pan_card" in r
               for r in result.exception_reasons if r.startswith("cross_source_mismatch"))


def test_low_ocr_on_key_field_routes_to_exception():
    ext = _clean_extractions()
    ext["aadhaar_card"]["aadhaar"] = _rec(VALID_AADHAAR, ocr=0.20)   # unreadable
    result = evaluate(ext)
    assert result.status == "exception"
    assert "low_confidence:aadhaar" in result.exception_reasons
    assert RiskFlag.LOW_OCR in result.field_confidence["aadhaar"].risk_flags


def test_missing_key_field_routes_to_exception():
    ext = _clean_extractions()
    del ext["pan_card"]["pan"]
    del ext["form16"]["pan"]   # pan now on no document
    result = evaluate(ext)
    assert result.status == "exception"
    assert "missing_key_field:pan" in result.exception_reasons


def test_format_invalid_id_routes_to_exception():
    ext = _clean_extractions()
    ext["pan_card"]["pan"] = _rec("INVALID99")   # fails PAN structure
    ext["form16"]["pan"] = _rec("INVALID99")
    result = evaluate(ext)
    assert result.status == "exception"
    assert RiskFlag.FORMAT_INVALID in result.field_confidence["pan"].risk_flags
    assert result.field_confidence["pan"].is_reliable is False


def test_payslip_obvious_fake_makes_income_unreliable():
    ext = _clean_extractions()
    # net > gross is structurally impossible → IMPLAUSIBLE_VALUE
    ext["salary_slips"]["gross_monthly_income"] = _rec(50000)
    ext["salary_slips"]["net_monthly_income"] = _rec(80000)
    result = evaluate(ext)
    assert result.status == "exception"
    income = result.field_confidence["gross_monthly_income"]
    assert income.is_reliable is False
    assert RiskFlag.IMPLAUSIBLE_VALUE in income.risk_flags


def test_single_source_non_key_field_does_not_block_verification():
    # address is only on the Aadhaar (single source) — neutral, must not penalize.
    result = evaluate(_clean_extractions())
    assert result.status == "verified"
    assert "address" in result.profile
    # address got no cross-check (single source)
    assert all(c.field_name != "address" for c in result.cross_checks)


# ---------------------------------------------------------------------------
# Layer 3: the agent entry — persistence, KYC, audit, routing
# ---------------------------------------------------------------------------

def _stores():
    engine = make_engine()
    return ApplicationRepository(engine), AuditStore(engine)


def _seed_with_docs(repo, doc_types):
    app = Application(applicant=Applicant(full_name="Priya Sharma"))
    app.features = {"documents": {d: {"uploaded": True, "verified": None} for d in doc_types}}
    repo.save(app)
    return app.application_id


def _fake_extract(extractions):
    return lambda application_id, doc_type: extractions.get(doc_type, {})


def test_verify_documents_clean_persists_profile_and_verifies():
    repo, audit = _stores()
    ext = _clean_extractions()
    app_id = _seed_with_docs(repo, ext.keys())

    result = verify_documents(repo, audit, app_id, extract=_fake_extract(ext))
    assert result.status == "verified"

    app = repo.get(app_id)
    # profile written onto applicant + features
    assert app.applicant.pan == "ABCDE1234F"
    assert app.applicant.aadhaar == VALID_AADHAAR
    assert app.applicant.current_address.startswith("12 MG Road")
    assert app.features["gross_monthly_income"] == 90000
    assert app.features["monthly_income"] == 90000           # rules-engine sync
    # KYC record populated + status verified
    assert app.kyc.status == KycStatus.VERIFIED
    names = {fc.field_name for fc in app.kyc.field_confidence}
    assert KEY_FIELDS <= names
    # each contributing document marked verified
    assert app.features["documents"]["aadhaar_card"]["verified"] is True
    assert app.features["documents"]["form16"]["verified"] is True

    # exactly one agent_reasoning event, carrying cross-checks + reasons
    events = [e for e in audit.reconstruct(app_id) if e.event_type == "agent_reasoning"]
    assert len(events) == 1
    assert events[0].payload["status"] == "verified"
    assert events[0].payload["cross_checks"]


def test_verify_documents_low_confidence_routes_to_kyc_exception():
    repo, audit = _stores()
    ext = _clean_extractions()
    ext["aadhaar_card"]["aadhaar"] = _rec(VALID_AADHAAR, ocr=0.15)
    app_id = _seed_with_docs(repo, ext.keys())

    result = verify_documents(repo, audit, app_id, extract=_fake_extract(ext))
    assert result.status == "exception"
    assert "low_confidence:aadhaar" in result.exception_reasons

    app = repo.get(app_id)
    # not verified → KYC left PENDING (human review via KYC_EXCEPTION, not terminal)
    assert app.kyc.status == KycStatus.PENDING
    # the Aadhaar document is marked NOT verified
    assert app.features["documents"]["aadhaar_card"]["verified"] is False
    events = [e for e in audit.reconstruct(app_id) if e.event_type == "agent_reasoning"]
    assert events[0].payload["status"] == "exception"
    assert events[0].payload["exception_reasons"]


def test_claimed_pan_mismatch_routes_to_kyc_exception():
    # applicant typed a PAN that disagrees with the document → claimed-vs-documented
    # mismatch on a key field → KYC_EXCEPTION (#claimed-cross-check).
    repo, audit = _stores()
    ext = _clean_extractions()                       # documents say PAN ABCDE1234F
    app = Application(applicant=Applicant(full_name="Priya Sharma", pan="ZZZZZ9999Z"))
    app.features = {"documents": {d: {"uploaded": True, "verified": None} for d in ext.keys()}}
    repo.save(app)

    result = verify_documents(repo, audit, app.application_id, extract=_fake_extract(ext))
    assert result.status == "exception"
    assert any(r.startswith("cross_source_mismatch:pan") and "applicant_form" in r
               for r in result.exception_reasons)
    # the applicant's claim is NEVER clobbered by the document, and the documented
    # value is kept separately so the underwriter can compare the two (#19 issue-1).
    saved = repo.get(app.application_id)
    assert saved.applicant.pan == "ZZZZZ9999Z"                            # claim preserved
    assert saved.features["documented_identity"]["pan"] == "ABCDE1234F"   # document recorded


def test_claimed_values_matching_documents_stay_verified():
    repo, audit = _stores()
    ext = _clean_extractions()
    app = Application(applicant=Applicant(
        full_name="Priya Sharma", pan="ABCDE1234F", aadhaar=VALID_AADHAAR))
    app.features = {"documents": {d: {"uploaded": True, "verified": None} for d in ext.keys()}}
    repo.save(app)

    result = verify_documents(repo, audit, app.application_id, extract=_fake_extract(ext))
    assert result.status == "verified"
    # the claimed-vs-documents cross-checks are recorded + matched in the audit
    ev = [e for e in audit.reconstruct(app.application_id) if e.event_type == "agent_reasoning"][0]
    form_checks = [c for c in ev.payload["cross_checks"] if c["source_a"] == "applicant_form"]
    assert form_checks and all(c["matches"] for c in form_checks)
    # claim preserved on the applicant; documented identity recorded alongside.
    saved = repo.get(app.application_id)
    assert saved.applicant.pan == "ABCDE1234F"
    assert saved.features["documented_identity"]["pan"] == "ABCDE1234F"


def test_verify_documents_requires_uploaded_documents():
    repo, audit = _stores()
    app = Application(applicant=Applicant(full_name="Priya"))
    repo.save(app)
    with pytest.raises(ValueError, match="no uploaded documents"):
        verify_documents(repo, audit, app.application_id, extract=_fake_extract({}))


def test_verify_documents_unknown_application():
    repo, audit = _stores()
    with pytest.raises(ValueError, match="unknown application"):
        verify_documents(repo, audit, "nope", extract=_fake_extract({}))


# ---------------------------------------------------------------------------
# OCR-adapter-backed extractor (default path, #9 will register the real adapter)
# ---------------------------------------------------------------------------

def test_make_ocr_extractor_calls_harness_per_document():
    from lending.adapters import AdapterHarness, MockAdapter

    harness = AdapterHarness()
    harness.register(MockAdapter("ocr", {
        "extract:aadhaar_card": {"name": _rec("Priya Sharma"), "aadhaar": _rec(VALID_AADHAAR)},
    }))
    extract = make_ocr_extractor(harness)
    data = extract("app-1", "aadhaar_card")
    assert data["aadhaar"]["value"] == VALID_AADHAAR


def test_mock_ocr_harness_drives_a_verified_application():
    """The placeholder mock OCR (pending #9) verifies a full clean doc set."""
    from lending.adapters.ocr_mock import _CLEAN_FIXTURES, make_mock_ocr_harness

    repo, audit = _stores()
    doc_types = [p.split(":", 1)[1] for p in _CLEAN_FIXTURES]
    app_id = _seed_with_docs(repo, doc_types)

    extract = make_ocr_extractor(make_mock_ocr_harness())
    result = verify_documents(repo, audit, app_id, extract=extract)
    assert result.status == "verified"


def test_worker_build_doc_extractor_mock_vs_live():
    from lending.workflow.worker import build_doc_extractor

    repo, _ = _stores()
    extract = build_doc_extractor("mock", repo)
    # unknown application → valid-format fallbacks (so KYC can still run)
    assert extract("app-1", "aadhaar_card")["aadhaar"]["value"] == VALID_AADHAAR
    # live now builds the LLM extractor (#9) instead of raising — it's callable
    # (it would hit the store + Gemini at call time, which we don't invoke here).
    assert callable(build_doc_extractor("live", repo))


def test_reflective_ocr_echoes_real_applicant_name():
    """The mock OCR must reflect the actual applicant, not a fixed profile (#41)."""
    from lending.adapters.ocr_mock import make_reflective_ocr_extractor

    repo, audit = _stores()
    app = Application(applicant=Applicant(full_name="Ravi Kumar"))
    app.features = {"documents": {d: {"uploaded": True, "verified": None} for d in
                                  ["aadhaar_card", "pan_card", "salary_slips", "bank_statement", "form16"]},
                    "monthly_income": 75000, "employer_name": "Infosys"}
    repo.save(app)

    extract = make_reflective_ocr_extractor(repo)
    assert extract(app.application_id, "aadhaar_card")["name"]["value"] == "Ravi Kumar"

    result = verify_documents(repo, audit, app.application_id, extract=extract)
    assert result.status == "verified"
    # the verified profile keeps the REAL applicant name (no fixed "Priya Sharma")
    assert repo.get(app.application_id).applicant.full_name == "Ravi Kumar"
