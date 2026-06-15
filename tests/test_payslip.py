"""
Isolation tests for payslip obvious-fake checks (#5 / §16.7).

Each fake-check fixture must raise its specific flag; a clean payslip passes
with no flags.
"""
import pytest
from lending.confidence import Payslip, RiskFlag, check_payslip


# ---------------------------------------------------------------------------
# Clean payslip — no flags
# ---------------------------------------------------------------------------

def clean_payslip() -> Payslip:
    return Payslip(
        gross_pay=50_000,
        net_pay=42_000,
        earnings={"basic": 25_000, "hra": 15_000, "special": 10_000},   # sums to 50_000
        deductions={"pf": 3_000, "tax": 5_000},                          # 50_000 - 8_000 = 42_000
    )


def test_clean_payslip_no_flags():
    assert check_payslip(clean_payslip()) == []


# ---------------------------------------------------------------------------
# COMPONENT_SUM_MISMATCH
# ---------------------------------------------------------------------------

def test_component_sum_mismatch():
    p = Payslip(
        gross_pay=50_000,
        net_pay=42_000,
        earnings={"basic": 25_000, "hra": 15_000, "special": 5_000},  # sums to 45_000, not 50_000
        deductions={"pf": 3_000, "tax": 5_000},
    )
    assert RiskFlag.COMPONENT_SUM_MISMATCH in check_payslip(p)


def test_component_sum_within_tolerance_ok():
    p = Payslip(
        gross_pay=50_000,
        net_pay=42_000,
        earnings={"basic": 25_000, "hra": 15_000, "special": 10_000.5},  # 0.5 slack < tol 1.0
        deductions={"pf": 3_000, "tax": 5_000.5},
    )
    assert RiskFlag.COMPONENT_SUM_MISMATCH not in check_payslip(p)


# ---------------------------------------------------------------------------
# NET_DERIVATION_MISMATCH
# ---------------------------------------------------------------------------

def test_net_derivation_mismatch():
    p = Payslip(
        gross_pay=50_000,
        net_pay=48_000,   # but 50_000 - 8_000 = 42_000
        earnings={"basic": 25_000, "hra": 15_000, "special": 10_000},
        deductions={"pf": 3_000, "tax": 5_000},
    )
    assert RiskFlag.NET_DERIVATION_MISMATCH in check_payslip(p)


# ---------------------------------------------------------------------------
# IMPLAUSIBLE_VALUE
# ---------------------------------------------------------------------------

def test_net_greater_than_gross_implausible():
    p = Payslip(gross_pay=40_000, net_pay=50_000, earnings={}, deductions={})
    assert RiskFlag.IMPLAUSIBLE_VALUE in check_payslip(p)


def test_negative_value_implausible():
    p = Payslip(gross_pay=50_000, net_pay=42_000,
                earnings={"basic": -25_000, "hra": 75_000}, deductions={"pf": 3_000, "tax": 5_000})
    assert RiskFlag.IMPLAUSIBLE_VALUE in check_payslip(p)


def test_gross_below_floor_implausible():
    p = Payslip(gross_pay=1_000, net_pay=900, earnings={}, deductions={"x": 100})
    assert RiskFlag.IMPLAUSIBLE_VALUE in check_payslip(p)


def test_gross_above_ceiling_implausible():
    p = Payslip(gross_pay=50_000_000, net_pay=40_000_000, earnings={}, deductions={"x": 10_000_000})
    assert RiskFlag.IMPLAUSIBLE_VALUE in check_payslip(p)


# ---------------------------------------------------------------------------
# Multiple flags can co-occur
# ---------------------------------------------------------------------------

def test_multiple_flags():
    p = Payslip(
        gross_pay=50_000,
        net_pay=60_000,  # net > gross → IMPLAUSIBLE + NET_DERIVATION
        earnings={"basic": 10_000},  # sums to 10_000 not 50_000 → COMPONENT_SUM
        deductions={"pf": 3_000},
    )
    flags = check_payslip(p)
    assert RiskFlag.IMPLAUSIBLE_VALUE in flags
    assert RiskFlag.COMPONENT_SUM_MISMATCH in flags
    assert RiskFlag.NET_DERIVATION_MISMATCH in flags


# ---------------------------------------------------------------------------
# No earnings breakdown → component check skipped, net check still runs
# ---------------------------------------------------------------------------

def test_no_earnings_breakdown_skips_component_check():
    p = Payslip(gross_pay=50_000, net_pay=42_000, earnings={}, deductions={"pf": 3_000, "tax": 5_000})
    flags = check_payslip(p)
    assert RiskFlag.COMPONENT_SUM_MISMATCH not in flags
    assert flags == []  # net derivation holds, plausible


# ---------------------------------------------------------------------------
# Version guard
# ---------------------------------------------------------------------------

def test_unknown_policy_version_raises():
    with pytest.raises(ValueError, match="Unknown policy_version"):
        check_payslip(clean_payslip(), policy_version="v99")
