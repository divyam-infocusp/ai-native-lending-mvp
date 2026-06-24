"""
Credit Bureau adapter (#10) — idempotent hard inquiry.

Returns a credit score + total monthly obligations + tradelines. The hard
inquiry is **idempotent** via the S0 harness (#1): a repeated pull for the same
application never re-inquires — a second hard pull would ding the applicant's
score and is a compliance problem, so the idempotency key
(application_id + provider + purpose) guarantees exactly one underlying inquiry.

Mock + real-ready: the real adapter implements `_execute` against the bureau API
and returns the same dict shape `pull_bureau` parses; the mock returns fixtures.
The consent gate (#8) is enforced by the *caller* (the Underwriting Agent #20)
before the pull — the adapter itself performs no authorization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .base import AdapterRequest
from .mock import MockAdapter
from .registry import AdapterHarness

BUREAU_PROVIDER = "bureau"
HARD_INQUIRY = "hard_inquiry"


@dataclass(frozen=True)
class Tradeline:
    lender: str
    kind: str            # "credit_card" | "personal_loan" | "auto_loan" | ...
    monthly_emi: float
    outstanding: float
    status: str          # "active" | "closed" | "delinquent"


@dataclass(frozen=True)
class BureauReport:
    score: Optional[int]                 # e.g. CIBIL 300–900; None for a thin/no-hit file
    has_record: bool                     # False → thin file (no credit history)
    total_monthly_obligations: float
    tradelines: list[Tradeline] = field(default_factory=list)
    report_id: Optional[str] = None


def _parse(data: dict) -> BureauReport:
    return BureauReport(
        score=data.get("score"),
        has_record=data.get("has_record", True),
        total_monthly_obligations=data.get("total_monthly_obligations", 0.0),
        tradelines=[Tradeline(**t) for t in data.get("tradelines", [])],
        report_id=data.get("report_id"),
    )


def pull_bureau(harness: AdapterHarness, application_id: str, *, purpose: str = HARD_INQUIRY) -> BureauReport:
    """Idempotent hard inquiry → typed BureauReport. Repeated calls for the same
    application return the cached report (exactly one underlying inquiry)."""
    resp = harness.call(AdapterRequest(application_id, BUREAU_PROVIDER, purpose))
    return _parse(resp.data)


# ---------------------------------------------------------------------------
# Mock fixtures (pending the real bureau integration) — demo/test profiles.
# ---------------------------------------------------------------------------

# A healthy file: good score, light obligations.
CLEAN_REPORT: dict = {
    "score": 700,
    "has_record": True,
    "total_monthly_obligations": 3_000.0,
    "tradelines": [
        {"lender": "HDFC", "kind": "credit_card", "monthly_emi": 0.0, "outstanding": 12_000.0, "status": "active"},
        {"lender": "Bajaj Finance", "kind": "consumer_durable", "monthly_emi": 3_000.0, "outstanding": 36_000.0, "status": "active"},
    ],
    "report_id": "CIBIL-DEMO-CLEAN",
}

# A thin file: no credit history → routes to UW_EXCEPTION in #20.
THIN_FILE_REPORT: dict = {
    "score": None,
    "has_record": False,
    "total_monthly_obligations": 0.0,
    "tradelines": [],
    "report_id": "CIBIL-DEMO-THIN",
}


def make_mock_bureau_harness(fixtures: Optional[dict] = None) -> AdapterHarness:
    """An AdapterHarness with a single mock bureau adapter registered."""
    harness = AdapterHarness()
    harness.register(MockAdapter(BUREAU_PROVIDER, fixtures or {HARD_INQUIRY: CLEAN_REPORT}))
    return harness
