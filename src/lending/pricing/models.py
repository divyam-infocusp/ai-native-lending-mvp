from dataclasses import dataclass
from typing import Optional

from lending.scorecard.models import RiskBand


@dataclass(frozen=True)
class Offer:
    rate: float        # annual interest rate, %
    amount: float      # offered principal, INR
    tenure: int        # months
    emi: float         # equated monthly installment, INR


@dataclass(frozen=True)
class PricingSensitivityResult:
    """Income-haircut sensitivity at the pricing/decision layer (§16.8).

    `sensitive` (and therefore `refer`) is True when discounting income flips
    the risk band or lendability — i.e. the approval leaned on an unverified
    income figure, so it should go to a human instead of auto-pricing.
    """
    sensitive: bool
    refer: bool
    original_band: RiskBand
    stressed_band: RiskBand
    original_offer: Optional[Offer]
    stressed_offer: Optional[Offer]
