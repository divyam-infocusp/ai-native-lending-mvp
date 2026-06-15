"""
Isolation tests for the Scorecard (#4).

Covers: band boundaries, income-haircut sensitivity, version guard,
monotonicity, and haircut validation.
"""
import pytest
from lending.rules_engine.models import ApplicantFeatures
from lending.scorecard import RiskBand, income_sensitivity, score

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

BASE = ApplicantFeatures(
    age=30,
    monthly_income=50_000,
    monthly_obligations=5_000,
    cibil_score=720,
    employment_tenure_months=24,
    loan_amount_requested=300_000,
    loan_tenure_months=36,
    is_salaried=True,
    has_cibil_record=True,
)


def tweak(**kwargs) -> ApplicantFeatures:
    d = {f: getattr(BASE, f) for f in BASE.__dataclass_fields__}
    d.update(kwargs)
    return ApplicantFeatures(**d)


# ---------------------------------------------------------------------------
# Basic scoring
# ---------------------------------------------------------------------------

def test_clean_applicant_scores_and_bands():
    result = score(BASE)
    assert result.score > 0
    assert result.band in {RiskBand.A, RiskBand.B, RiskBand.C, RiskBand.D}


def test_score_is_integer():
    result = score(BASE)
    assert isinstance(result.score, int)


# ---------------------------------------------------------------------------
# Band thresholds (v1: A≥90, B≥70, C≥50, D≥30, X<30)
# ---------------------------------------------------------------------------

def test_excellent_profile_gets_band_a():
    # Max CIBIL, high income, low DTI, long tenure
    f = tweak(
        cibil_score=800,
        monthly_income=100_000,
        monthly_obligations=1_000,
        employment_tenure_months=60,
        loan_amount_requested=200_000,
    )
    result = score(f)
    assert result.band == RiskBand.A, f"Expected A, got {result.band} (score={result.score})"


def test_poor_profile_gets_band_x():
    # Minimum CIBIL (just at 650), lowest income, high DTI, short tenure
    f = tweak(
        cibil_score=650,
        monthly_income=20_000,
        monthly_obligations=15_000,
        employment_tenure_months=6,
        loan_amount_requested=500_000,
        loan_tenure_months=12,
    )
    result = score(f)
    assert result.band == RiskBand.X, f"Expected X, got {result.band} (score={result.score})"


# ---------------------------------------------------------------------------
# Monotonicity: better profile → higher or equal score
# ---------------------------------------------------------------------------

def test_higher_cibil_increases_score():
    low = score(tweak(cibil_score=650))
    high = score(tweak(cibil_score=800))
    assert high.score >= low.score


def test_higher_income_increases_score():
    low = score(tweak(monthly_income=25_000))
    high = score(tweak(monthly_income=80_000))
    assert high.score >= low.score


def test_lower_dti_increases_score():
    high_dti = score(tweak(monthly_obligations=20_000))
    low_dti = score(tweak(monthly_obligations=1_000))
    assert low_dti.score >= high_dti.score


# ---------------------------------------------------------------------------
# Income sensitivity (§16.8)
# ---------------------------------------------------------------------------

def test_no_haircut_not_sensitive():
    result = income_sensitivity(BASE, haircut_pct=0.0)
    assert result.sensitive is False
    assert result.original_band == result.stressed_band
    assert result.original_score == result.stressed_score


def test_large_haircut_may_flip_band():
    # A marginal profile with 50% income haircut should become sensitive
    f = tweak(
        cibil_score=670,
        monthly_income=25_000,
        monthly_obligations=3_000,
        employment_tenure_months=8,
    )
    result = income_sensitivity(f, haircut_pct=0.50)
    # With 50% income haircut both income points and DTI worsen — should flip
    assert result.stressed_score <= result.original_score


def test_sensitivity_detects_band_flip():
    # Build a profile right at a band boundary and stress it
    f = tweak(cibil_score=800, monthly_income=100_000, monthly_obligations=1_000,
              employment_tenure_months=60, loan_amount_requested=200_000)
    base_result = score(f)
    # 30% haircut on a Band A profile
    result = income_sensitivity(f, haircut_pct=0.30)
    if result.stressed_band != base_result.band:
        assert result.sensitive is True
    else:
        assert result.sensitive is False


def test_sensitivity_result_fields_populated():
    result = income_sensitivity(BASE, haircut_pct=0.10)
    assert result.original_score > 0
    assert result.stressed_score > 0
    assert result.original_band in RiskBand.__members__.values()
    assert result.stressed_band in RiskBand.__members__.values()


def test_stressed_score_lte_original():
    """Income haircut can only reduce score, never increase it."""
    result = income_sensitivity(BASE, haircut_pct=0.20)
    assert result.stressed_score <= result.original_score


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------

def test_unknown_version_raises():
    with pytest.raises(ValueError, match="Unknown scorecard_version"):
        score(BASE, scorecard_version="v99")


def test_invalid_haircut_raises():
    with pytest.raises(ValueError, match="haircut_pct"):
        income_sensitivity(BASE, haircut_pct=1.0)


def test_negative_haircut_raises():
    with pytest.raises(ValueError, match="haircut_pct"):
        income_sensitivity(BASE, haircut_pct=-0.1)
