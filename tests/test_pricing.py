"""
Isolation tests for the Pricing Engine (#12 / §16.8).

Covers: band → rate fixtures, the three amount caps (request / band ceiling /
affordability), tenure clamping, EMI correctness, non-priceable band, version
guard, and income-sensitivity (flip → refer vs stable → not flagged).
"""
import pytest

from lending.policy import PRICING_POLICY
from lending.pricing import Offer, affordability_cap, emi, income_sensitivity, price
from lending.rules_engine.models import ApplicantFeatures
from lending.scorecard import RiskBand

# ---------------------------------------------------------------------------
# Fixtures
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
# Rate per band (config-driven)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("band,expected_rate", [
    (RiskBand.A, 10.5),
    (RiskBand.B, 14.5),
    (RiskBand.C, 18.0),
    (RiskBand.D, 22.0),
])
def test_rate_per_band(band, expected_rate):
    offer = price(BASE, band)
    assert offer.rate == expected_rate


def test_better_band_cheaper_rate():
    assert price(BASE, RiskBand.A).rate < price(BASE, RiskBand.D).rate


# ---------------------------------------------------------------------------
# Amount = min(requested, band cap, affordability)
# ---------------------------------------------------------------------------

def test_amount_bound_by_request():
    # Small request, high income → request is the binding cap
    f = tweak(loan_amount_requested=100_000, monthly_income=200_000, monthly_obligations=0)
    assert price(f, RiskBand.A).amount == 100_000


def test_amount_bound_by_band_ceiling():
    # Huge request + huge income (affordability not binding) → band D ceiling caps it
    f = tweak(loan_amount_requested=10_000_000, monthly_income=5_000_000, monthly_obligations=0)
    assert price(f, RiskBand.D).amount == PRICING_POLICY["v1"]["band_max_amount"]["D"]


def test_amount_bound_by_affordability():
    # Big request, modest income → affordability binds; the resulting EMI should
    # sit right at the DTI headroom.
    f = tweak(loan_amount_requested=10_000_000, monthly_income=50_000, monthly_obligations=5_000)
    offer = price(f, RiskBand.B)
    assert offer.amount < f.loan_amount_requested
    headroom = 0.50 * 50_000 - 5_000  # = 20_000
    assert offer.emi == pytest.approx(headroom, abs=5)  # EMI pinned to the budget


def test_debt_heavy_gets_smaller_amount():
    light = price(tweak(monthly_obligations=2_000, loan_amount_requested=10_000_000), RiskBand.B)
    heavy = price(tweak(monthly_obligations=20_000, loan_amount_requested=10_000_000), RiskBand.B)
    assert heavy.amount < light.amount


def test_maxed_out_gets_zero_amount():
    # Existing obligations already exceed the DTI ceiling → no capacity
    f = tweak(monthly_obligations=30_000, monthly_income=50_000, loan_amount_requested=10_000_000)
    offer = price(f, RiskBand.B)
    assert offer.amount == 0
    assert offer.emi == 0


# ---------------------------------------------------------------------------
# Tenure clamping
# ---------------------------------------------------------------------------

def test_tenure_clamped_to_bounds():
    assert price(tweak(loan_tenure_months=6), RiskBand.B).tenure == 12   # floor
    assert price(tweak(loan_tenure_months=120), RiskBand.B).tenure == 60  # ceiling
    assert price(tweak(loan_tenure_months=36), RiskBand.B).tenure == 36   # within


def test_longer_tenure_raises_affordability():
    short = price(tweak(loan_tenure_months=12, loan_amount_requested=10_000_000), RiskBand.B)
    long = price(tweak(loan_tenure_months=60, loan_amount_requested=10_000_000), RiskBand.B)
    assert long.amount > short.amount


# ---------------------------------------------------------------------------
# EMI correctness (amortization ↔ affordability are inverses)
# ---------------------------------------------------------------------------

def test_emi_known_value():
    # 3,00,000 @ 14.5% over 36 months ≈ 10,326.29 (standard amortization)
    assert emi(300_000, 14.5, 36) == pytest.approx(10_326.29, abs=1.0)


def test_emi_greater_than_straight_division():
    # With interest, EMI must exceed principal/months
    assert emi(300_000, 14.5, 36) > 300_000 / 36


def test_affordability_and_emi_are_inverse():
    # The principal affordable at a given EMI, repriced, returns ~that EMI
    cap = affordability_cap(50_000, 5_000, 14.5, 36, 0.50)  # headroom 20_000
    assert emi(cap, 14.5, 36) == pytest.approx(20_000, abs=1.0)


def test_zero_interest_emi_is_straight_division():
    assert emi(120_000, 0.0, 12) == pytest.approx(10_000)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_non_priceable_band_raises():
    with pytest.raises(ValueError, match="not priceable"):
        price(BASE, RiskBand.X)


def test_unknown_version_raises():
    with pytest.raises(ValueError, match="Unknown pricing_version"):
        price(BASE, RiskBand.B, pricing_version="v99")


# ---------------------------------------------------------------------------
# Income sensitivity → refer (§16.8)
# ---------------------------------------------------------------------------

def test_stable_case_not_flagged():
    # Strong, comfortable profile: a haircut won't flip the band
    f = tweak(cibil_score=800, monthly_income=100_000, monthly_obligations=1_000,
              employment_tenure_months=60, loan_amount_requested=200_000)
    result = income_sensitivity(f, haircut_pct=0.10)
    assert result.sensitive is False
    assert result.refer is False
    assert result.original_offer is not None


def test_sensitive_case_flagged_refer():
    # Borderline profile where a 50% income haircut flips the band → refer.
    # Asserted unconditionally: the fixture must actually flip, then be flagged.
    f = tweak(cibil_score=700, monthly_income=30_000, monthly_obligations=2_000,
              employment_tenure_months=10, loan_amount_requested=150_000)
    result = income_sensitivity(f, haircut_pct=0.50)
    assert result.original_band != result.stressed_band, "fixture must flip the band"
    assert result.sensitive is True
    assert result.refer is True


def test_sensitivity_defaults_haircut_from_config():
    # No explicit haircut → uses the scorecard policy value, no error
    result = income_sensitivity(BASE)
    assert isinstance(result.sensitive, bool)
    assert result.original_offer is not None
