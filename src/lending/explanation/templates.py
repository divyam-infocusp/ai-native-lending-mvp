"""
Human-reviewed adverse-action / explanation templates (#17, §16.11).

Binding legal text is template-sourced, keyed by (reason_code, language) — never
free-translated. Numbers are code-inserted into the {placeholders} at render
time, so the same figure appears identically across languages.

Each template carries a `signature`: a stable, unique static phrase guaranteed
to appear in any rendering of it. The faithfulness check (§16.1) uses signatures
to detect, from a block of text, exactly which reason codes it covers.
"""
from __future__ import annotations

from typing import NamedTuple


class Template(NamedTuple):
    text: str        # may contain {placeholders} for code-inserted numbers
    signature: str   # unique static phrase, present verbatim in any rendering


# (reason_code, language) → Template
TEMPLATES: dict[tuple[str, str], Template] = {
    # ---- English ----
    ("LOW_CIBIL", "en"): Template(
        "Your credit bureau score of {cibil_score} is below the required minimum of {min_cibil_score}.",
        "credit bureau score",
    ),
    ("INSUFFICIENT_INCOME", "en"): Template(
        "Your declared monthly income of {monthly_income} is below the required minimum of {min_monthly_income}.",
        "declared monthly income",
    ),
    ("HIGH_DTI", "en"): Template(
        "Your total monthly debt obligations exceed the permitted limit of {max_dti_pct}% of income.",
        "total monthly debt obligations",
    ),
    ("SHORT_EMPLOYMENT", "en"): Template(
        "Your current employment tenure of {employment_tenure_months} months is below the required minimum of {min_employment_months} months.",
        "current employment tenure",
    ),
    ("LOAN_AMOUNT_EXCEEDS_LIMIT", "en"): Template(
        "The requested loan amount of {loan_amount_requested} exceeds the maximum permitted amount of {max_loan_amount}.",
        "requested loan amount",
    ),
    ("UNDERAGE", "en"): Template(
        "The applicant's age of {age} is below the minimum eligible age of {min_age}.",
        "below the minimum eligible age",
    ),
    ("OVERAGE", "en"): Template(
        "The applicant's age of {age} is above the maximum eligible age of {max_age}.",
        "above the maximum eligible age",
    ),
    ("NOT_SALARIED", "en"): Template(
        "The applicant is not currently in salaried employment.",
        "not currently in salaried employment",
    ),
    ("NO_CIBIL_RECORD", "en"): Template(
        "No credit bureau record was found for the applicant.",
        "No credit bureau record was found",
    ),
    # ---- Hindi (subset, to demonstrate per-language selection with code-inserted numbers) ----
    ("LOW_CIBIL", "hi"): Template(
        "आपका क्रेडिट ब्यूरो स्कोर {cibil_score} आवश्यक न्यूनतम {min_cibil_score} से कम है।",
        "क्रेडिट ब्यूरो स्कोर",
    ),
    ("INSUFFICIENT_INCOME", "hi"): Template(
        "आपकी घोषित मासिक आय {monthly_income} आवश्यक न्यूनतम {min_monthly_income} से कम है।",
        "घोषित मासिक आय",
    ),
}

SUPPORTED_LANGUAGES = {"en", "hi"}
