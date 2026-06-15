"""
Tests for versioned policy config wiring (§16.9).

Verifies the engines actually read from lending.policy rather than hardcoded
constants: editing config changes behavior, and config defaults are applied.
"""
import pytest

import lending.policy as policy
from lending.confidence import field_confidence
from lending.rules_engine import ApplicantFeatures, DispositionHint, evaluate
from lending.scorecard import income_sensitivity, score

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CLEAN = ApplicantFeatures(
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


# ---------------------------------------------------------------------------
# Config shape sanity
# ---------------------------------------------------------------------------

def test_v1_present_in_every_domain():
    assert "v1" in policy.RULES_POLICY
    assert "v1" in policy.SCORECARD_POLICY
    assert "v1" in policy.CONFIDENCE_POLICY


def test_scorecard_feature_names_match_extractors():
    from lending.scorecard.scorecard import _EXTRACTORS
    for name in policy.SCORECARD_POLICY["v1"]["features"]:
        assert name in _EXTRACTORS, f"no extractor for configured feature {name!r}"


# ---------------------------------------------------------------------------
# Rules Engine reads thresholds from config (monkeypatch proves the wiring)
# ---------------------------------------------------------------------------

def test_rules_engine_reads_min_cibil_from_config(monkeypatch):
    f = ApplicantFeatures(**{**{k: getattr(CLEAN, k) for k in CLEAN.__dataclass_fields__},
                             "cibil_score": 680})
    # 680 passes default 650
    assert evaluate(f).disposition_hint != DispositionHint.DECLINE
    # Raise the configured floor above 680 → now a knockout
    patched = {**policy.RULES_POLICY, "v1": {**policy.RULES_POLICY["v1"], "min_cibil_score": 700}}
    monkeypatch.setattr(policy, "RULES_POLICY", patched)
    # engine module imported the dict by reference; patch the name it bound too
    monkeypatch.setattr("lending.rules_engine.engine.RULES_POLICY", patched)
    result = evaluate(f)
    assert result.disposition_hint == DispositionHint.DECLINE
    assert "LOW_CIBIL" in [h.reason_code for h in result.policy_hits]


# ---------------------------------------------------------------------------
# Scorecard haircut now defaults from config (§16.8)
# ---------------------------------------------------------------------------

def test_income_sensitivity_defaults_haircut_from_config():
    # Calling without haircut_pct must use the configured value, not error.
    result = income_sensitivity(CLEAN)
    configured = policy.SCORECARD_POLICY["v1"]["income_haircut_pct"]
    # Reproduce by passing the configured value explicitly — identical outcome.
    explicit = income_sensitivity(CLEAN, haircut_pct=configured)
    assert result.stressed_score == explicit.stressed_score
    assert result.sensitive == explicit.sensitive


def test_income_sensitivity_explicit_overrides_config():
    default_result = income_sensitivity(CLEAN)
    big = income_sensitivity(CLEAN, haircut_pct=0.50)
    # A larger haircut can only lower (or equal) the stressed score
    assert big.stressed_score <= default_result.stressed_score


# ---------------------------------------------------------------------------
# Confidence Service version guard + config-driven threshold
# ---------------------------------------------------------------------------

def test_confidence_unknown_policy_version_raises():
    with pytest.raises(ValueError, match="Unknown policy_version"):
        field_confidence(ocr_conf=0.9, cross_source_checks=[], validators=[], policy_version="v99")


def test_confidence_uses_config_threshold_by_default():
    configured = policy.CONFIDENCE_POLICY["v1"]["threshold"]
    # ocr just below configured threshold (no checks/validators) → unreliable
    just_below = field_confidence(ocr_conf=configured - 0.01, cross_source_checks=[], validators=[])
    assert just_below.is_reliable is False
    # ocr at/above threshold → reliable
    at_or_above = field_confidence(ocr_conf=configured, cross_source_checks=[], validators=[])
    assert at_or_above.is_reliable is True
