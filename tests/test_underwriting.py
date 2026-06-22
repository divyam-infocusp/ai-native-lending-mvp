"""
Tests for the Underwriting Agent (#20).

Covers: feature assembly from bureau + stated data → expected engine inputs;
read-only engine use (no decision written); thin-file and data-gap → UW_EXCEPTION;
consent gate enforcement; and decision reproducibility from the assembled inputs.
"""
from lending.adapters import make_mock_bureau_harness
from lending.adapters.bureau import CLEAN_REPORT, HARD_INQUIRY, THIN_FILE_REPORT
from lending.agents import BUREAU_PULL_PURPOSE, assemble_features, underwrite
from lending.audit import AuditStore
from lending.consent import capture_authorization
from lending.decision import decide
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.rules_engine import ApplicantFeatures

# Stated/verified data on the application (what KYC + onboarding leave behind).
STATED = {
    "age": 32,
    "monthly_income": 90_000,
    "employment_tenure_months": 60,
    "loan_amount_requested": 300_000,
    "loan_tenure_months": 36,
    "is_salaried": True,
}


def _stores():
    engine = make_engine()
    return ApplicationRepository(engine), AuditStore(engine)


def _seed(repo, stated=None, *, consent=True):
    app = Application(applicant=Applicant(full_name="Priya Sharma"),
                      features=dict(stated if stated is not None else STATED))
    repo.save(app)
    if consent:
        capture_authorization(app, BUREAU_PULL_PURPOSE)
        repo.save(app)
    return app.application_id


def _bureau(report=None):
    return make_mock_bureau_harness({HARD_INQUIRY: report or CLEAN_REPORT})


# ---------------------------------------------------------------------------
# Feature assembly
# ---------------------------------------------------------------------------

def test_assemble_features_merges_bureau_and_stated_data():
    repo, _ = _stores()
    app_id = _seed(repo)
    app = repo.get(app_id)
    from lending.adapters import pull_bureau
    report = pull_bureau(_bureau(), app_id)

    features, missing = assemble_features(app, report)
    assert missing == []
    # credit data from the bureau
    assert features.cibil_score == 780
    assert features.monthly_obligations == 3_000.0
    assert features.has_cibil_record is True
    # stated data from the application
    assert features.monthly_income == 90_000
    assert features.loan_amount_requested == 300_000
    assert features.is_salaried is True


def test_assemble_features_reports_data_gap():
    repo, _ = _stores()
    # missing loan_amount_requested + employment_tenure_months
    app_id = _seed(repo, {"age": 32, "monthly_income": 90_000, "is_salaried": True})
    app = repo.get(app_id)
    from lending.adapters import pull_bureau
    report = pull_bureau(_bureau(), app_id)

    features, missing = assemble_features(app, report)
    assert features is None
    assert "loan_amount_requested" in missing
    assert "employment_tenure_months" in missing


def test_assemble_infers_is_salaried_from_employment_type():
    repo, _ = _stores()
    stated = {**STATED}
    del stated["is_salaried"]
    stated["employment_type"] = "salaried"
    app_id = _seed(repo, stated)
    app = repo.get(app_id)
    from lending.adapters import pull_bureau
    features, missing = assemble_features(app, pull_bureau(_bureau(), app_id))
    assert missing == []
    assert features.is_salaried is True


# ---------------------------------------------------------------------------
# The agent — happy path, read-only, reproducibility
# ---------------------------------------------------------------------------

def test_underwrite_completes_and_writes_summary_not_decision():
    repo, audit = _stores()
    app_id = _seed(repo)

    result = underwrite(repo, audit, app_id, bureau_harness=_bureau())
    assert result.status == "completed"
    assert result.summary["bureau_score"] == 780
    assert result.summary["dti"] == round(3_000 / 90_000, 4)
    assert result.summary["band"]

    app = repo.get(app_id)
    # engine inputs persisted for reproducibility
    assert app.features["cibil_score"] == 780
    assert app.features["underwriting_summary"]["dti"] == round(3_000 / 90_000, 4)
    # READ-ONLY: the agent never writes the decision (that is #18's job)
    assert app.decision is None

    events = [e for e in audit.reconstruct(app_id) if e.event_type == "agent_reasoning"]
    assert events[-1].payload["agent"] == "underwriting"
    assert events[-1].payload["status"] == "completed"


def test_decision_reproducible_from_assembled_inputs():
    repo, audit = _stores()
    app_id = _seed(repo)
    underwrite(repo, audit, app_id, bureau_harness=_bureau())

    feats = repo.get(app_id).features
    inputs = {k: feats[k] for k in (
        "age", "monthly_income", "monthly_obligations", "cibil_score",
        "employment_tenure_months", "loan_amount_requested", "loan_tenure_months",
        "is_salaried", "has_cibil_record",
    )}
    # Deterministic engine: same assembled inputs → identical decision twice.
    d1 = decide(ApplicantFeatures(**inputs))
    d2 = decide(ApplicantFeatures(**inputs))
    assert d1.disposition == d2.disposition
    assert d1.score == d2.score
    assert d1.disposition.value == "approve"   # clean applicant


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

def test_thin_file_routes_to_uw_exception():
    repo, audit = _stores()
    app_id = _seed(repo)
    result = underwrite(repo, audit, app_id, bureau_harness=_bureau(THIN_FILE_REPORT))
    assert result.status == "exception"
    assert result.reasons == ["thin_file"]
    assert repo.get(app_id).decision is None


def test_data_gap_routes_to_uw_exception():
    repo, audit = _stores()
    app_id = _seed(repo, {"age": 32, "monthly_income": 90_000, "is_salaried": True})
    result = underwrite(repo, audit, app_id, bureau_harness=_bureau())
    assert result.status == "exception"
    assert any(r.startswith("data_gap:") for r in result.reasons)


def test_missing_consent_routes_to_uw_exception():
    repo, audit = _stores()
    app_id = _seed(repo, consent=False)        # no Layer-1 authorization captured
    result = underwrite(repo, audit, app_id, bureau_harness=_bureau())
    assert result.status == "exception"
    assert any(r.startswith("consent:") for r in result.reasons)


def test_low_score_bureau_drives_decline_downstream():
    """Underwriting just assembles; the engine (read-only) reflects a poor file."""
    repo, audit = _stores()
    app_id = _seed(repo)
    low = {**CLEAN_REPORT, "score": 600}       # below the CIBIL knockout
    result = underwrite(repo, audit, app_id, bureau_harness=_bureau(low))
    assert result.status == "completed"
    # the read-only preview already shows the knockout reason
    assert "LOW_CIBIL" in result.summary["reason_codes"]
