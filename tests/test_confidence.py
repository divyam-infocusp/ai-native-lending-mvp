"""
Isolation tests for the Confidence Service (#5 / §16.4).

Verifies: composite formula, flag assignment, reliability verdict,
edge cases (no checks, boundary values), and input validation.
"""
import pytest
from lending.confidence import (
    CrossSourceCheck,
    FieldConfidenceResult,
    RiskFlag,
    ValidatorResult,
    field_confidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def agree(field: str = "pan", source_a: str = "aadhaar_xml", source_b: str = "pan_card_ocr") -> CrossSourceCheck:
    return CrossSourceCheck(field_name=field, source_a=source_a, source_b=source_b, matches=True)


def disagree(field: str = "pan", source_a: str = "aadhaar_xml", source_b: str = "pan_card_ocr") -> CrossSourceCheck:
    return CrossSourceCheck(field_name=field, source_a=source_a, source_b=source_b, matches=False)


def valid(field: str = "pan") -> ValidatorResult:
    return ValidatorResult(field_name=field, valid=True)


def invalid(field: str = "pan") -> ValidatorResult:
    return ValidatorResult(field_name=field, valid=False)


# ---------------------------------------------------------------------------
# Happy path — all signals perfect
# ---------------------------------------------------------------------------

def test_perfect_signals_reliable():
    result = field_confidence(
        ocr_conf=1.0,
        cross_source_checks=[agree(), agree()],
        validators=[valid(), valid()],
    )
    assert result.confidence == pytest.approx(1.0)
    assert result.risk_flags == []
    assert result.is_reliable is True


# ---------------------------------------------------------------------------
# Composite formula verification
# ---------------------------------------------------------------------------

def test_composite_is_product_of_three_signals():
    ocr = 0.9
    checks = [agree(), agree(), disagree()]   # agreement_ratio = 2/3
    vals = [valid(), invalid()]               # validator_ratio = 1/2
    expected = ocr * (2 / 3) * (1 / 2)
    result = field_confidence(ocr_conf=ocr, cross_source_checks=checks, validators=vals)
    assert result.confidence == pytest.approx(expected, rel=1e-5)


# ---------------------------------------------------------------------------
# Risk flag: LOW_OCR
# ---------------------------------------------------------------------------

def test_low_ocr_fires_flag():
    result = field_confidence(ocr_conf=0.50, cross_source_checks=[], validators=[])
    assert RiskFlag.LOW_OCR in result.risk_flags


def test_ocr_exactly_at_threshold_no_flag():
    result = field_confidence(ocr_conf=0.60, cross_source_checks=[], validators=[])
    assert RiskFlag.LOW_OCR not in result.risk_flags


# ---------------------------------------------------------------------------
# Risk flag: CROSS_SOURCE_MISMATCH
# ---------------------------------------------------------------------------

def test_mismatch_fires_flag():
    result = field_confidence(ocr_conf=0.95, cross_source_checks=[disagree()], validators=[])
    assert RiskFlag.CROSS_SOURCE_MISMATCH in result.risk_flags


def test_all_agree_no_mismatch_flag():
    result = field_confidence(ocr_conf=0.95, cross_source_checks=[agree(), agree()], validators=[])
    assert RiskFlag.CROSS_SOURCE_MISMATCH not in result.risk_flags


def test_failed_check_retains_source_provenance():
    """A mismatch must be traceable to the two sources that disagreed (audit)."""
    checks = [
        agree("name", source_a="pan_card_ocr", source_b="cibil_bureau"),
        disagree("date_of_birth", source_a="aadhaar_xml", source_b="bank_statement"),
    ]
    result = field_confidence(ocr_conf=0.95, cross_source_checks=checks, validators=[])
    assert RiskFlag.CROSS_SOURCE_MISMATCH in result.risk_flags
    # Caller can recover exactly which sources disagreed from the input checks
    failed = [c for c in checks if not c.matches]
    assert len(failed) == 1
    assert failed[0].field_name == "date_of_birth"
    assert failed[0].source_a == "aadhaar_xml"
    assert failed[0].source_b == "bank_statement"


# ---------------------------------------------------------------------------
# Risk flag: FORMAT_INVALID
# ---------------------------------------------------------------------------

def test_format_invalid_fires_flag():
    result = field_confidence(ocr_conf=0.95, cross_source_checks=[], validators=[invalid()])
    assert RiskFlag.FORMAT_INVALID in result.risk_flags


def test_all_valid_no_format_flag():
    result = field_confidence(ocr_conf=0.95, cross_source_checks=[], validators=[valid()])
    assert RiskFlag.FORMAT_INVALID not in result.risk_flags


# ---------------------------------------------------------------------------
# Risk flag: CONFIDENCE_BELOW_THRESHOLD
# ---------------------------------------------------------------------------

def test_below_threshold_flag_fires():
    result = field_confidence(ocr_conf=0.50, cross_source_checks=[], validators=[], threshold=0.70)
    assert RiskFlag.CONFIDENCE_BELOW_THRESHOLD in result.risk_flags


def test_above_threshold_no_flag():
    result = field_confidence(ocr_conf=1.0, cross_source_checks=[agree()], validators=[valid()], threshold=0.70)
    assert RiskFlag.CONFIDENCE_BELOW_THRESHOLD not in result.risk_flags


# ---------------------------------------------------------------------------
# Reliability: FORMAT_INVALID makes field unreliable even if confidence is high
# ---------------------------------------------------------------------------

def test_format_invalid_overrides_high_confidence():
    # High OCR, agreements all pass, but format/checksum fails → unreliable
    result = field_confidence(
        ocr_conf=0.95,
        cross_source_checks=[agree(), agree()],
        validators=[invalid()],
        threshold=0.50,
    )
    assert result.is_reliable is False


def test_reliable_requires_both_threshold_and_no_format_failure():
    result = field_confidence(
        ocr_conf=1.0,
        cross_source_checks=[agree()],
        validators=[valid()],
        threshold=0.70,
    )
    assert result.is_reliable is True


# ---------------------------------------------------------------------------
# Edge cases: empty lists
# ---------------------------------------------------------------------------

def test_no_cross_source_checks_treated_as_full_agreement():
    result = field_confidence(ocr_conf=1.0, cross_source_checks=[], validators=[valid()])
    assert RiskFlag.CROSS_SOURCE_MISMATCH not in result.risk_flags


def test_no_validators_treated_as_all_pass():
    result = field_confidence(ocr_conf=1.0, cross_source_checks=[agree()], validators=[])
    assert RiskFlag.FORMAT_INVALID not in result.risk_flags


def test_no_checks_no_validators_confidence_equals_ocr():
    result = field_confidence(ocr_conf=0.85, cross_source_checks=[], validators=[])
    assert result.confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Custom threshold
# ---------------------------------------------------------------------------

def test_custom_threshold_affects_reliability():
    result_strict = field_confidence(ocr_conf=0.75, cross_source_checks=[], validators=[], threshold=0.80)
    result_lenient = field_confidence(ocr_conf=0.75, cross_source_checks=[], validators=[], threshold=0.60)
    assert result_strict.is_reliable is False
    assert result_lenient.is_reliable is True


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

def test_ocr_above_1_raises():
    with pytest.raises(ValueError, match="ocr_conf"):
        field_confidence(ocr_conf=1.1, cross_source_checks=[], validators=[])


def test_ocr_below_0_raises():
    with pytest.raises(ValueError, match="ocr_conf"):
        field_confidence(ocr_conf=-0.1, cross_source_checks=[], validators=[])


def test_invalid_threshold_raises():
    with pytest.raises(ValueError, match="threshold"):
        field_confidence(ocr_conf=0.9, cross_source_checks=[], validators=[], threshold=1.5)
