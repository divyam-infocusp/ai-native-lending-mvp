"""
Human-readable policy view (read-only) — assembles the versioned policy config
(policy.py, §16.9) + the rule engine's hard/soft classification into a labelled
structure the Ops Console can render, so an underwriter can see exactly which
thresholds and fields the deterministic engine is applying.

Pure + presentation-only: it never changes policy; it explains it.
"""
from __future__ import annotations

from lending.policy import CONFIDENCE_POLICY, PRICING_POLICY, RULES_POLICY, SCORECARD_POLICY
from lending.rules_engine import knockout_reason_codes

# Presentation descriptors for each eligibility rule, in evaluation order:
#   (reason_code, label, RULES_POLICY key | None, unit, plain-language description)
_RULES: list[tuple] = [
    ("UNDERAGE", "Minimum age", "min_age", "yrs",
     "The applicant must be at least this old."),
    ("OVERAGE", "Maximum age", "max_age", "yrs",
     "The applicant must be no older than this."),
    ("NOT_SALARIED", "Salaried employment", None, "",
     "Only salaried applicants are eligible in this pilot."),
    ("NO_CIBIL_RECORD", "Credit bureau record", None, "",
     "The applicant must have an existing credit history."),
    ("LOW_CIBIL", "Minimum credit score", "min_cibil_score", "",
     "CIBIL bureau score must be at least this."),
    ("INSUFFICIENT_INCOME", "Minimum monthly income", "min_monthly_income", "₹",
     "Gross monthly income must be at least this."),
    ("SHORT_EMPLOYMENT", "Minimum employment tenure", "min_employment_months", "months",
     "Months in current employment."),
    ("HIGH_DTI", "Maximum debt-to-income (DTI)", "max_dti", "ratio",
     "Post-loan EMIs + existing obligations as a share of income."),
    ("LOAN_AMOUNT_EXCEEDS_LIMIT", "Maximum loan amount", "max_loan_amount", "₹",
     "The largest principal the policy allows."),
]


def build_policy_view(version: str = "v1") -> dict:
    """Assemble the labelled, UI-ready policy view for a policy version."""
    if version not in RULES_POLICY:
        raise ValueError(f"unknown policy version: {version!r}")

    hard = knockout_reason_codes(version)
    rp = RULES_POLICY[version]
    rules = [
        {
            "reason_code": code,
            "label": label,
            "threshold": (rp.get(key) if key else None),
            "unit": unit,
            "description": desc,
            "type": "hard" if code in hard else "soft",
        }
        for code, label, key, unit, desc in _RULES
    ]

    sc = SCORECARD_POLICY[version]
    pr = PRICING_POLICY[version]
    # One risk-band table: min score to reach the band + its price/ceiling.
    bands = [
        {
            "band": band,
            "min_score": min_score,
            "rate_pct": pr["band_rates"].get(band),
            "max_amount": pr["band_max_amount"].get(band),
        }
        for min_score, band in sc["band_thresholds"]
    ]

    cf = CONFIDENCE_POLICY[version]
    return {
        "version": version,
        "rules": rules,
        "bands": bands,
        "scorecard": {
            "min_score": sc["min_score"],
            "income_haircut_pct": sc["income_haircut_pct"],
        },
        "pricing": {
            "tenure_min_months": pr["tenure_min_months"],
            "tenure_max_months": pr["tenure_max_months"],
            "affordability_dti": pr["affordability_dti"],
            "processing_fee_pct": pr["processing_fee_pct"],
            "gst_pct": pr["gst_pct"],
            "offer_validity_days": pr["offer_validity_days"],
        },
        "documents": {
            "min_confidence": cf["threshold"],
            "min_ocr_conf": cf["min_ocr_conf"],
            "name_match_min_ratio": cf["name_match_min_ratio"],
            "income_match_tolerance_pct": cf["income_match_tolerance_pct"],
            "key_fields": list(cf["kyc_key_fields"]),
        },
    }
