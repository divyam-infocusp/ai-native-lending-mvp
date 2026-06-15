"""
Confidence Service — grounded field-level confidence scoring (§16.4).

Confidence is computed from three observable, non-LLM signals:
  1. ocr_conf     — optical character recognition confidence [0, 1]
  2. cross_source — list of cross-source agreement checks
  3. validators   — list of format/checksum validator results

Final composite = ocr_conf × agreement_ratio × validator_ratio

Risk flags are attached for each failing dimension.
No LLM self-report is used — this is the "grounded" guarantee.
"""

from .models import CrossSourceCheck, FieldConfidenceResult, RiskFlag, ValidatorResult

# Minimum composite confidence to declare a field reliable
_DEFAULT_THRESHOLD = 0.70

# Minimum OCR confidence before LOW_OCR flag fires
_MIN_OCR_CONF = 0.60


def field_confidence(
    ocr_conf: float,
    cross_source_checks: list[CrossSourceCheck],
    validators: list[ValidatorResult],
    threshold: float = _DEFAULT_THRESHOLD,
) -> FieldConfidenceResult:
    """
    Compute composite field confidence from three grounded signals.

    Args:
        ocr_conf: Raw OCR confidence for this field [0.0, 1.0].
        cross_source_checks: Agreement checks across independent sources.
        validators: Format/checksum validator results for this field.
        threshold: Minimum composite confidence for is_reliable=True.

    Returns:
        FieldConfidenceResult with composite confidence, risk flags, and reliability verdict.
    """
    if not (0.0 <= ocr_conf <= 1.0):
        raise ValueError(f"ocr_conf must be in [0, 1]: got {ocr_conf}")
    if not (0.0 <= threshold <= 1.0):
        raise ValueError(f"threshold must be in [0, 1]: got {threshold}")

    risk_flags: list[RiskFlag] = []

    # --- Signal 1: OCR confidence ---
    if ocr_conf < _MIN_OCR_CONF:
        risk_flags.append(RiskFlag.LOW_OCR)

    # --- Signal 2: Cross-source agreement ratio ---
    if cross_source_checks:
        matches = sum(1 for c in cross_source_checks if c.matches)
        agreement_ratio = matches / len(cross_source_checks)
        if agreement_ratio < 1.0:
            risk_flags.append(RiskFlag.CROSS_SOURCE_MISMATCH)
    else:
        agreement_ratio = 1.0  # no checks to fail

    # --- Signal 3: Validator pass ratio ---
    if validators:
        passed = sum(1 for v in validators if v.valid)
        validator_ratio = passed / len(validators)
        if validator_ratio < 1.0:
            risk_flags.append(RiskFlag.FORMAT_INVALID)
    else:
        validator_ratio = 1.0  # no validators to fail

    # --- Composite ---
    composite = ocr_conf * agreement_ratio * validator_ratio

    if composite < threshold:
        risk_flags.append(RiskFlag.CONFIDENCE_BELOW_THRESHOLD)

    is_reliable = composite >= threshold and RiskFlag.FORMAT_INVALID not in risk_flags

    return FieldConfidenceResult(
        confidence=round(composite, 6),
        risk_flags=risk_flags,
        is_reliable=is_reliable,
    )
