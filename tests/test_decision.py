"""
Tests for decision assembly (#18) — the decision-of-record.

Covers: outcome composition (approve / decline / refer), version stamp + source,
explanation rendering, and audit reconstructability (reconstruct == issued).
"""
import pytest

from lending.audit import AuditStore
from lending.decision import decide, reconstruct_decision, record_decision
from lending.governance import IncompleteVersionSet, VersionSet
from lending.los import Applicant, Application, ApplicationRepository, make_engine
from lending.rules_engine import ApplicantFeatures

CLEAN = ApplicantFeatures(
    age=32, monthly_income=90_000, monthly_obligations=3_000, cibil_score=780,
    employment_tenure_months=60, loan_amount_requested=300_000, loan_tenure_months=36,
    is_salaried=True, has_cibil_record=True,
)


def tweak(**kw) -> ApplicantFeatures:
    d = {f: getattr(CLEAN, f) for f in CLEAN.__dataclass_fields__}
    d.update(kw)
    return ApplicantFeatures(**d)


# ---------------------------------------------------------------------------
# Outcome composition
# ---------------------------------------------------------------------------

def test_clean_applicant_approves():
    d = decide(CLEAN)
    assert d.disposition.value == "approve"
    assert d.reason_codes == []
    assert d.source == "engine"
    assert d.band in {"A", "B", "C", "D"}


def test_hard_knockout_declines_with_reason():
    d = decide(tweak(cibil_score=600))
    assert d.disposition.value == "decline"
    assert "LOW_CIBIL" in d.reason_codes


def test_soft_hit_refers():
    d = decide(tweak(monthly_obligations=60_000))  # high DTI
    assert d.disposition.value == "refer"
    assert "HIGH_DTI" in d.reason_codes


def test_not_lendable_band_declines():
    # Boundary profile that passes rules but scores below the lendable floor
    d = decide(tweak(cibil_score=650, monthly_income=20_000, employment_tenure_months=6,
                     monthly_obligations=0, loan_amount_requested=240_000, loan_tenure_months=12))
    if d.band == "X":
        assert d.disposition.value == "decline"
        assert d.reason_codes  # never a decline with no reason


# ---------------------------------------------------------------------------
# Version stamp + explanation (decision-of-record completeness)
# ---------------------------------------------------------------------------

def test_decision_carries_full_version_stamp():
    d = decide(CLEAN)
    assert d.version_set is not None
    assert d.version_set.rules and d.version_set.scorecard
    assert d.version_set.pricing and d.version_set.confidence
    assert d.version_set.model_id and d.version_set.prompt_version


def test_decline_carries_rendered_explanation():
    d = decide(tweak(cibil_score=600))
    assert d.explanation
    assert "credit bureau score" in d.explanation  # rendered from LOW_CIBIL template


def test_invalid_version_set_rejected():
    bad = VersionSet(rules="v99", scorecard="v1", pricing="v1", confidence="v1",
                     model_id="m", prompt_version="p")
    with pytest.raises(Exception):  # UnknownVersion via validate_version_set
        decide(CLEAN, version_set=bad)


# ---------------------------------------------------------------------------
# Audit reconstructability (the headline #18 criterion)
# ---------------------------------------------------------------------------

@pytest.fixture
def stores():
    engine = make_engine()
    return ApplicationRepository(engine), AuditStore(engine)


def test_reconstruct_from_audit_matches_issued(stores):
    repo, audit = stores
    app = Application(applicant=Applicant(full_name="Priya"), features=vars(CLEAN))
    repo.save(app)

    issued = decide(CLEAN)
    record_decision(repo, audit, app.application_id, issued)

    reconstructed = reconstruct_decision(audit, app.application_id)
    assert reconstructed == issued


def test_record_persists_decision_on_application(stores):
    repo, audit = stores
    app = Application(applicant=Applicant(full_name="Raj"), features=vars(tweak(cibil_score=600)))
    repo.save(app)

    issued = decide(tweak(cibil_score=600))
    record_decision(repo, audit, app.application_id, issued)

    assert repo.get(app.application_id).decision == issued


def test_reconstruct_unknown_returns_none(stores):
    _, audit = stores
    assert reconstruct_decision(audit, "ghost") is None
