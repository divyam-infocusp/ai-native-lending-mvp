"""
Scorecard — weighted, versioned credit scoring.

Each version defines:
  - A list of (feature_extractor, weight, bins) triples.
  - Band thresholds mapping score ranges to RiskBand.

The engine sums weighted bin points across all features.
"""
from dataclasses import dataclass
from typing import Callable

from lending.rules_engine.models import ApplicantFeatures

from .models import RiskBand, ScoreResult, SensitivityResult


# ---------------------------------------------------------------------------
# Scorecard internals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScorecardFeature:
    name: str
    extractor: Callable[[ApplicantFeatures], float]
    # bins: list of (upper_bound_exclusive, points) sorted asc; last bin catches remainder
    bins: list[tuple[float, int]]
    weight: float


def _bin_points(value: float, bins: list[tuple[float, int]]) -> int:
    for upper, points in bins:
        if value < upper:
            return points
    # last bin
    return bins[-1][1]


@dataclass(frozen=True)
class ScorecardSpec:
    features: list[ScorecardFeature]
    band_thresholds: list[tuple[int, RiskBand]]  # (min_score_inclusive, band) desc order
    min_score: int                                # below → band X


# ---------------------------------------------------------------------------
# v1 scorecard definition
# ---------------------------------------------------------------------------

def _dti_ratio(f: ApplicantFeatures) -> float:
    emi = (f.loan_amount_requested / f.loan_tenure_months) if f.loan_tenure_months > 0 else 0
    return (f.monthly_obligations + emi) / f.monthly_income if f.monthly_income > 0 else 1.0


_SCORECARD_V1 = ScorecardSpec(
    features=[
        ScorecardFeature(
            name="cibil_score",
            extractor=lambda f: float(f.cibil_score),
            bins=[
                (650, 0),
                (700, 15),
                (725, 25),
                (750, 35),
                (775, 45),
                (float("inf"), 55),
            ],
            weight=1.0,
        ),
        ScorecardFeature(
            name="monthly_income",
            extractor=lambda f: f.monthly_income,
            bins=[
                (20_000, 0),
                (30_000, 5),
                (50_000, 10),
                (75_000, 15),
                (float("inf"), 20),
            ],
            weight=1.0,
        ),
        ScorecardFeature(
            name="dti",
            extractor=_dti_ratio,
            bins=[
                (0.30, 20),
                (0.40, 15),
                (0.50, 5),
                (float("inf"), 0),
            ],
            weight=1.0,
        ),
        ScorecardFeature(
            name="employment_tenure_months",
            extractor=lambda f: float(f.employment_tenure_months),
            bins=[
                (6, 0),
                (12, 5),
                (24, 10),
                (float("inf"), 15),
            ],
            weight=1.0,
        ),
    ],
    # Band thresholds: checked in descending order
    band_thresholds=[
        (90, RiskBand.A),
        (70, RiskBand.B),
        (50, RiskBand.C),
        (30, RiskBand.D),
    ],
    min_score=30,
)

_SCORECARD_CATALOGUE: dict[str, ScorecardSpec] = {
    "v1": _SCORECARD_V1,
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def score(
    features: ApplicantFeatures,
    scorecard_version: str = "v1",
) -> ScoreResult:
    """Compute a credit score and assign a risk band."""
    if scorecard_version not in _SCORECARD_CATALOGUE:
        raise ValueError(f"Unknown scorecard_version: {scorecard_version!r}")

    spec = _SCORECARD_CATALOGUE[scorecard_version]
    total = 0
    for feat in spec.features:
        value = feat.extractor(features)
        points = _bin_points(value, feat.bins)
        total += int(points * feat.weight)

    if total < spec.min_score:
        return ScoreResult(score=total, band=RiskBand.X)

    band = RiskBand.D  # default to lowest lendable
    for threshold, b in spec.band_thresholds:
        if total >= threshold:
            band = b
            break

    return ScoreResult(score=total, band=band)


def income_sensitivity(
    features: ApplicantFeatures,
    haircut_pct: float,
    scorecard_version: str = "v1",
) -> SensitivityResult:
    """
    Re-score with income discounted by haircut_pct (e.g. 0.10 = 10% haircut).
    Returns whether the band or lendability outcome flips (§16.8).
    """
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
