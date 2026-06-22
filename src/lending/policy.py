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
# Pricing Engine — risk-based offer terms (§16.9)
#
# band_rates:        annual interest rate (%) per risk band — better band, lower rate.
# band_max_amount:   ceiling on principal per band (riskier bands borrow less).
# tenure bounds:     allowed loan duration (months); the request is clamped here.
# affordability_dti: max share of monthly income that may go to total debt; caps
#                    the offered amount so the new EMI stays within budget.
# Band X is intentionally absent — it is not lendable.
# ---------------------------------------------------------------------------
PRICING_POLICY: dict[str, dict] = {
    "v1": {
        "band_rates": {"A": 10.5, "B": 14.5, "C": 18.0, "D": 22.0},
        "band_max_amount": {"A": 2_000_000, "B": 1_000_000, "C": 500_000, "D": 200_000},
        "tenure_min_months": 12,
        "tenure_max_months": 60,
        "affordability_dti": 0.50,
        # Offer-letter terms (Decision QA + offer delivery, #23). Real Indian
        # personal-loan sanction-letter components; credit-policy values (§16.9).
        "processing_fee_pct": 0.02,     # 2% of sanctioned amount
        "gst_pct": 0.18,                # GST on the processing fee
        "offer_validity_days": 30,      # sanction letter validity window
    },
}


# ---------------------------------------------------------------------------
# Confidence Service — composite reliability thresholds (§16.4)
# ---------------------------------------------------------------------------
CONFIDENCE_POLICY: dict[str, dict] = {
    "v1": {
        "threshold": 0.70,      # min composite confidence for is_reliable
        "min_ocr_conf": 0.60,   # OCR confidence below this fires LOW_OCR
        # payslip obvious-fake checks
        "payslip_arithmetic_tolerance": 1.0,   # INR rounding slack on sums
        "payslip_min_gross": 5_000,            # implausibly low monthly gross
        "payslip_max_gross": 10_000_000,       # implausibly high monthly gross
        # cross-source field comparison (Document Intelligence, #19)
        "income_match_tolerance_pct": 0.10,    # money fields agree within 10% (bonuses/arrears)
        "name_match_min_ratio": 0.6,           # ≥60% of the shorter name's tokens must match
        # KYC key fields: must each be reliable (and agree across sources) or the
        # application routes to KYC_EXCEPTION. A credit/compliance policy judgment
        # (§16.9), versioned here rather than hardcoded in the agent.
        "kyc_key_fields": (
            "name", "date_of_birth", "pan", "aadhaar", "gross_monthly_income",
        ),
    },
}


# ---------------------------------------------------------------------------
# Consent — two-layer gate parameters (§16.6)
# ---------------------------------------------------------------------------
CONSENT_POLICY: dict[str, dict] = {
    "v1": {
        # A Layer-2 per-pull artifact is only valid for this long after minting;
        # an older one is stale and may not authorize a pull (forces a fresh
        # per-pull artifact each time, per §16.6).
        "l2_freshness_seconds": 300,
    },
}
