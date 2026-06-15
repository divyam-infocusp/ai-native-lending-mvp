from dataclasses import dataclass
from enum import Enum


class RiskBand(str, Enum):
    A = "A"   # lowest risk
    B = "B"
    C = "C"
    D = "D"   # highest risk (still approvable)
    X = "X"   # below min score — not lendable


@dataclass(frozen=True)
class ScoreResult:
    score: int
    band: RiskBand


@dataclass(frozen=True)
class SensitivityResult:
    """Result of an income-haircut sensitivity test (§16.8)."""
    original_score: int
    original_band: RiskBand
    stressed_score: int
    stressed_band: RiskBand
    sensitive: bool  # True if band or approve/decline outcome flips
