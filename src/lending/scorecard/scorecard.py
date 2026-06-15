"""
Scorecard — weighted, versioned credit scoring.

Scoring *logic* lives here: how each feature value is extracted, how bin points
are summed, how bands are assigned. The *numbers* — bins, weights, band
thresholds, min score, income-haircut % — are read from the versioned
SCORECARD_POLICY config (§16.9), keyed by scorecard_version.
"""
from typing import Callable

from lending.policy import SCORECARD_POLICY
from lending.rules_engine.models import ApplicantFeatures

from .models import RiskBand, ScoreResult, SensitivityResult


# ---------------------------------------------------------------------------
# Scoring internals
# ---------------------------------------------------------------------------

def _bin_points(value: float, bins: list[tuple[float, int]]) -> int:
    """Bins are (upper_bound_exclusive, points) ascending; last is catch-all."""
    for upper, points in bins:
        if value < upper:
            return points
    return bins[-1][1]


def _dti_ratio(f: ApplicantFeatures) -> float:
    emi = (f.loan_amount_requested / f.loan_tenure_months) if f.loan_tenure_months > 0 else 0
    return (f.monthly_obligations + emi) / f.monthly_income if f.monthly_income > 0 else 1.0


# Feature extractors are *logic* and stay in code, keyed by the same names used
# in SCORECARD_POLICY[version]["features"]. The config supplies bins + weights.
_EXTRACTORS: dict[str, Callable[[ApplicantFeatures], float]] = {
    "cibil_score": lambda f: float(f.cibil_score),
    "monthly_income": lambda f: f.monthly_income,
    "dti": _dti_ratio,
    "employment_tenure_months": lambda f: float(f.employment_tenure_months),
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def score(
    features: ApplicantFeatures,
    scorecard_version: str = "v1",
) -> ScoreResult:
    """Compute a credit score and assign a risk band, per the versioned config."""
    if scorecard_version not in SCORECARD_POLICY:
        raise ValueError(f"Unknown scorecard_version: {scorecard_version!r}")

    cfg = SCORECARD_POLICY[scorecard_version]

    total = 0
    for name, fcfg in cfg["features"].items():
        extractor = _EXTRACTORS[name]
        value = extractor(features)
        points = _bin_points(value, fcfg["bins"])
        total += int(points * fcfg["weight"])

    if total < cfg["min_score"]:
        return ScoreResult(score=total, band=RiskBand.X)

    band = RiskBand.D  # safety fallback; lowest threshold normally catches all
    for threshold, band_name in cfg["band_thresholds"]:
        if total >= threshold:
            band = RiskBand(band_name)
            break

    return ScoreResult(score=total, band=band)


def income_sensitivity(
    features: ApplicantFeatures,
    haircut_pct: float | None = None,
    scorecard_version: str = "v1",
) -> SensitivityResult:
    """
    Re-score with income discounted by haircut_pct (e.g. 0.10 = 10% haircut)
    and report whether the band or lendability outcome flips (§16.8).

    haircut_pct defaults to the versioned policy value
    (SCORECARD_POLICY[version]["income_haircut_pct"]); pass an explicit value
    only for what-if analysis.
    """
    if scorecard_version not in SCORECARD_POLICY:
        raise ValueError(f"Unknown scorecard_version: {scorecard_version!r}")

    if haircut_pct is None:
        haircut_pct = SCORECARD_POLICY[scorecard_version]["income_haircut_pct"]

    if not (0.0 <= haircut_pct < 1.0):
        raise ValueError(f"haircut_pct must be in [0, 1): got {haircut_pct}")

    original = score(features, scorecard_version)

    stressed_income = features.monthly_income * (1 - haircut_pct)
    stressed_features = ApplicantFeatures(
        age=features.age,
        monthly_income=stressed_income,
        monthly_obligations=features.monthly_obligations,
        cibil_score=features.cibil_score,
        employment_tenure_months=features.employment_tenure_months,
        loan_amount_requested=features.loan_amount_requested,
        loan_tenure_months=features.loan_tenure_months,
        is_salaried=features.is_salaried,
        has_cibil_record=features.has_cibil_record,
    )
    stressed = score(stressed_features, scorecard_version)

    original_lendable = original.band != RiskBand.X
    stressed_lendable = stressed.band != RiskBand.X
    sensitive = (original.band != stressed.band) or (original_lendable != stressed_lendable)

    return SensitivityResult(
        original_score=original.score,
        original_band=original.band,
        stressed_score=stressed.score,
        stressed_band=stressed.band,
        sensitive=sensitive,
    )
