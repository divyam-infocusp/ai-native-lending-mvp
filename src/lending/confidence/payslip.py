"""
Payslip obvious-fake checks (pure, no I/O) — §16.7 "filter out obvious fakes".

These are deterministic, self-contained sanity checks on a single payslip:
  - COMPONENT_SUM_MISMATCH  : earnings components don't add up to stated gross
  - NET_DERIVATION_MISMATCH : gross - deductions doesn't equal stated net
  - IMPLAUSIBLE_VALUE       : negatives, net > gross, or gross out of sane range

Stateful fraud signals (document tamper detection, hash reuse across prior
submissions) are intentionally NOT here — they need persisted state and live
in a follow-up tied to the audit store (#6).

Thresholds/tolerances are read from the versioned CONFIDENCE_POLICY (§16.9).
"""
from dataclasses import dataclass, field

from lending.policy import CONFIDENCE_POLICY

from .models import RiskFlag


@dataclass(frozen=True)
class Payslip:
    gross_pay: float
    net_pay: float
    earnings: dict[str, float] = field(default_factory=dict)    # component -> amount
    deductions: dict[str, float] = field(default_factory=dict)  # component -> amount


def check_payslip(payslip: Payslip, policy_version: str = "v1") -> list[RiskFlag]:
    """Return the list of obvious-fake risk flags raised by this payslip.

    A clean, internally-consistent payslip returns an empty list.
    """
    if policy_version not in CONFIDENCE_POLICY:
        raise ValueError(f"Unknown policy_version: {policy_version!r}")

    cfg = CONFIDENCE_POLICY[policy_version]
    tol = cfg["payslip_arithmetic_tolerance"]
    min_gross = cfg["payslip_min_gross"]
    max_gross = cfg["payslip_max_gross"]

    flags: list[RiskFlag] = []

    # --- Plausibility (checked first; structural sanity) ---
    all_values = [payslip.gross_pay, payslip.net_pay, *payslip.earnings.values(), *payslip.deductions.values()]
    implausible = (
        any(v < 0 for v in all_values)
        or payslip.net_pay > payslip.gross_pay
        or not (min_gross <= payslip.gross_pay <= max_gross)
    )
    if implausible:
        flags.append(RiskFlag.IMPLAUSIBLE_VALUE)

    # --- Arithmetic: earnings components sum to gross ---
    if payslip.earnings:
        if abs(sum(payslip.earnings.values()) - payslip.gross_pay) > tol:
            flags.append(RiskFlag.COMPONENT_SUM_MISMATCH)

    # --- Arithmetic: gross - deductions == net ---
    expected_net = payslip.gross_pay - sum(payslip.deductions.values())
    if abs(expected_net - payslip.net_pay) > tol:
        flags.append(RiskFlag.NET_DERIVATION_MISMATCH)

    return flags
