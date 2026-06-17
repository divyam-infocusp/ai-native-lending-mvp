"""
Adverse-action / explanation renderer + faithfulness check (#17, §16.1/§16.11).

render()          turns a fired reason-code set + language into legal text, using
                  reviewed templates with code-inserted numbers.
verify_faithful() asserts a block of text covers EXACTLY the fired set — no
                  orphan claims (a reason in the text that wasn't fired) and no
                  omissions (a fired reason missing from the text).
render_faithful() renders then verifies, raising on mismatch (the reject path).
build_context()   assembles the number values from features + policy thresholds.
"""
from __future__ import annotations

from lending.policy import RULES_POLICY

from .models import (
    FaithfulnessError,
    MissingTemplateError,
    RenderedExplanation,
    RenderedSentence,
)
from .templates import TEMPLATES


def render(
    reason_codes: list[str],
    language: str = "en",
    context: dict | None = None,
) -> RenderedExplanation:
    context = context or {}
    sentences: list[RenderedSentence] = []
    for code in reason_codes:
        template = TEMPLATES.get((code, language))
        if template is None:
            raise MissingTemplateError(f"no template for ({code!r}, {language!r})")
        sentences.append(RenderedSentence(code, template.text.format_map(context)))
    text = " ".join(s.sentence for s in sentences)
    return RenderedExplanation(list(reason_codes), language, text, sentences)


def covered_reason_codes(text: str, language: str = "en") -> set[str]:
    """Detect which reason codes a block of text covers, via template signatures."""
    return {
        code
        for (code, lang), template in TEMPLATES.items()
        if lang == language and template.signature in text
    }


def verify_faithful(reason_codes: list[str], text: str, language: str = "en") -> bool:
    """True iff the text covers exactly the fired reason-code set."""
    return covered_reason_codes(text, language) == set(reason_codes)


def render_faithful(
    reason_codes: list[str],
    language: str = "en",
    context: dict | None = None,
) -> RenderedExplanation:
    rendered = render(reason_codes, language, context)
    if not verify_faithful(reason_codes, rendered.text, language):
        raise FaithfulnessError(
            f"rendered text does not cover exactly {sorted(set(reason_codes))}"
        )
    return rendered


def build_context(features: dict | None, rules_version: str = "v1") -> dict:
    """Number values for the templates, from the applicant's features + the
    versioned policy thresholds (numbers are code-inserted, never translated)."""
    policy = RULES_POLICY.get(rules_version, RULES_POLICY["v1"])
    f = features or {}
    return {
        "cibil_score": f.get("cibil_score"),
        "min_cibil_score": policy["min_cibil_score"],
        "monthly_income": f.get("monthly_income"),
        "min_monthly_income": policy["min_monthly_income"],
        "max_dti_pct": int(policy["max_dti"] * 100),
        "loan_amount_requested": f.get("loan_amount_requested"),
        "max_loan_amount": policy["max_loan_amount"],
        "age": f.get("age"),
        "min_age": policy["min_age"],
        "max_age": policy["max_age"],
        "employment_tenure_months": f.get("employment_tenure_months"),
        "min_employment_months": policy["min_employment_months"],
    }
