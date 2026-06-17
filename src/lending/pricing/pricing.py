"""
Pricing Engine (#12) — risk-based offer terms + income-haircut sensitivity.

Deterministic and versioned (§2.1, §16.9). Given the Scorecard's band and the
applicant's request, it produces {rate, amount, tenure, emi}:

  - rate   : looked up from the versioned band → rate table.
  - tenure : the requested tenure, clamped to policy bounds.
  - amount : min(requested, band ceiling, affordability cap) — the affordability
             cap is the largest principal whose EMI keeps total debt within the
             DTI limit, so we never offer a loan the applicant can't service.
  - emi    : standard amortization of (amount, rate, tenure) — pure math.

Policy numbers live in PRICING_POLICY (placeholder, pending risk-SME sign-off);
the amortization math is hard code.
"""
from __future__ import annotations

from lending.policy import PRICING_POLICY, SCORECARD_POLICY
from lending.rules_engine.models import ApplicantFeatures
from lending.scorecard import RiskBand, income_sensitivity as _scorecard_sensitivity

from .models import Offer, PricingSensitivityResult


# ---------------------------------------------------------------------------
# Math helpers (pure)
# ---------------------------------------------------------------------------

def _monthly_rate(annual_pct: float) -> float:
    return annual_pct / 100.0 / 12.0


def emi(principal: float, annual_pct: float, months: int) -> float:
    """Equated monthly installment via standard amortization."""
    if months <= 0:
        raise ValueError("months must be positive")
    if principal <= 0:
        return 0.0
    r = _monthly_rate(annual_pct)
    if r == 0:
        return principal / months
    factor = (1 + r) ** months
    return principal * r * factor / (factor - 1)


def affordability_cap(
    monthly_income: float,
    monthly_obligations: float,
    annual_pct: float,
    months: int,
    max_dti: float,
) -> float:
    """Largest principal whose EMI keeps total monthly debt within max_dti.

    Works backwards: headroom EMI = max_dti·income − existing obligations, then
    the present value of that EMI stream over the tenure.
    """
    headroom = max_dti * monthly_income - monthly_obligations
    if headroom <= 0:
        return 0.0
    r = _monthly_rate(annual_pct)
    if r == 0:
        return headroom * months
    factor = (1 + r) ** months
    return headroom * (factor - 1) / (r * factor)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def price(
    features: ApplicantFeatures,
    band: RiskBand,
    pricing_version: str = "v1",
) -> Offer:
    """Produce offer terms for a priceable band. Band X (not lendable) raises."""
    if pricing_version not in PRICING_POLICY:
        raise ValueError(f"Unknown pricing_version: {pricing_version!r}")

    cfg = PRICING_POLICY[pricing_version]
    band_key = band.value if isinstance(band, RiskBand) else str(band)
    if band_key not in cfg["band_rates"]:
        raise ValueError(f"band {band_key!r} is not priceable")

    rate = cfg["band_rates"][band_key]
    tenure = _clamp(
        int(features.loan_tenure_months),
        cfg["tenure_min_months"],
        cfg["tenure_max_months"],
    )

    cap = affordability_cap(
        features.monthly_income,
        features.monthly_obligations,
        rate,
        tenure,
        cfg["affordability_dti"],
    )
    amount = min(
        float(features.loan_amount_requested),
        float(cfg["band_max_amount"][band_key]),
        cap,
    )
    amount = float(int(amount))  # whole rupees, rounded down (never offer more than affordable)

    return Offer(rate=rate, amount=amount, tenure=tenure, emi=round(emi(amount, rate, tenure), 2))


def _haircut_features(features: ApplicantFeatures, haircut_pct: float) -> ApplicantFeatures:
    return ApplicantFeatures(
        age=features.age,
        monthly_income=features.monthly_income * (1 - haircut_pct),
        monthly_obligations=features.monthly_obligations,
        cibil_score=features.cibil_score,
        employment_tenure_months=features.employment_tenure_months,
        loan_amount_requested=features.loan_amount_requested,
        loan_tenure_months=features.loan_tenure_months,
        is_salaried=features.is_salaried,
        has_cibil_record=features.has_cibil_record,
    )


def _maybe_price(features: ApplicantFeatures, band: RiskBand, pricing_version: str) -> Offer | None:
    return None if band == RiskBand.X else price(features, band, pricing_version)


def income_sensitivity(
    features: ApplicantFeatures,
    haircut_pct: float | None = None,
    scorecard_version: str = "v1",
    pricing_version: str = "v1",
) -> PricingSensitivityResult:
    """Re-run scorecard+pricing with income discounted; flag band/lendability
    flips as income-sensitive → refer (§16.8)."""
    if haircut_pct is None:
        haircut_pct = SCORECARD_POLICY[scorecard_version]["income_haircut_pct"]

    sc = _scorecard_sensitivity(features, haircut_pct, scorecard_version)

    original_offer = _maybe_price(features, sc.original_band, pricing_version)
    stressed_offer = _maybe_price(
        _haircut_features(features, haircut_pct), sc.stressed_band, pricing_version
    )

    return PricingSensitivityResult(
        sensitive=sc.sensitive,
        refer=sc.sensitive,
        original_band=sc.original_band,
        stressed_band=sc.stressed_band,
        original_offer=original_offer,
        stressed_offer=stressed_offer,
    )
