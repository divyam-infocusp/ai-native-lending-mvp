from dataclasses import dataclass, field
from enum import Enum


class RiskFlag(str, Enum):
    LOW_OCR = "LOW_OCR"                      # OCR confidence below threshold
    CROSS_SOURCE_MISMATCH = "CROSS_SOURCE_MISMATCH"  # field differs across sources
    FORMAT_INVALID = "FORMAT_INVALID"        # format/checksum validator failed
    CONFIDENCE_BELOW_THRESHOLD = "CONFIDENCE_BELOW_THRESHOLD"  # composite too low


@dataclass(frozen=True)
class CrossSourceCheck:
    """Result of comparing a field value across two data sources."""
    field_name: str
    matches: bool  # True if sources agree


@dataclass(frozen=True)
class ValidatorResult:
    """Result of a format/checksum validator for a field."""
    field_name: str
    valid: bool  # True if format/checksum is correct


@dataclass(frozen=True)
class FieldConfidenceResult:
    confidence: float         # 0.0–1.0 composite confidence
    risk_flags: list[RiskFlag]
    is_reliable: bool         # True when confidence >= threshold and no critical flags
