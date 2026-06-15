"""
Versioned policy configuration — risk-SME-owned parameters (§16.9).

⚠️  PLACEHOLDER VALUES. Every number in this file is an engineering placeholder
pending sign-off from a risk/credit SME. These are NOT vetted thresholds.

They live here — not hardcoded inside the engines — so that:
  - the risk SME owns the *numbers*; engineering owns the *logic* that reads them;
  - a change produces a new version key and is reproducible/auditable;
  - the deterministic core stays testable against known inputs.

The full governance machinery — per-application version pinning, change audit,
and a non-engineer editing surface — is tracked in issue #7. This module is its
seed: a flat, version-keyed lookup the engines read at evaluation time.

What stays in code vs. here:
  - Rule *logic* (which rules are hard knockouts, their reason codes, the
    comparisons themselves) lives in the engines.
  - Only the *numbers* and scoring *bins* live here.

Bands are stored as plain strings ("A".."D") so this module has no internal
imports and migrates cleanly to YAML/DB later.
"""

INF = float("inf")


# ---------------------------------------------------------------------------
# Rules Engine — hard knockout + soft policy thresholds
# ---------------------------------------------------------------------------
RULES_POLICY: dict[str, dict] = {
    "v1": {
        "min_age": 21,
        "max_age": 60,
        "min_cibil_score": 650,
        "min_monthly_income": 20_000,
        "min_employment_months": 6,
        "max_dti": 0.50,
        "max_loan_amount": 2_000_000,
    },
}


# ---------------------------------------------------------------------------
# Scorecard — per-feature weighted bins, band thresholds, haircut
#
# bins: list of (upper_bound_exclusive, points), ascending; last entry is the
#       catch-all for everything at or above the previous bound.
# band_thresholds: (min_score_inclusive, band) in descending order.
# income_haircut_pct: the §16.8 sensitivity-test discount applied to income.
# ---------------------------------------------------------------------------
SCORECARD_POLICY: dict[str, dict] = {
    "v1": {
        "features": {
            "cibil_score": {
                "weight": 1.0,
                "bins": [(650, 0), (700, 15), (725, 25), (750, 35), (775, 45), (INF, 55)],
            },
            "monthly_income": {
                "weight": 1.0,
                "bins": [(20_000, 0), (30_000, 5), (50_000, 10), (75_000, 15), (INF, 20)],
            },
            "dti": {
                "weight": 1.0,
                "bins": [(0.30, 20), (0.40, 15), (0.50, 5), (INF, 0)],
            },
            "employment_tenure_months": {
                "weight": 1.0,
                "bins": [(6, 0), (12, 5), (24, 10), (INF, 15)],
            },
        },
        "band_thresholds": [(90, "A"), (70, "B"), (50, "C"), (30, "D")],
        "min_score": 30,
        "income_haircut_pct": 0.10,
    },
}


# ---------------------------------------------------------------------------
# Confidence Service — composite reliability thresholds (§16.4)
# ---------------------------------------------------------------------------
CONFIDENCE_POLICY: dict[str, dict] = {
    "v1": {
        "threshold": 0.70,      # min composite confidence for is_reliable
        "min_ocr_conf": 0.60,   # OCR confidence below this fires LOW_OCR
    },
}
