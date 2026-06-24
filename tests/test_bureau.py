"""
Tests for the Credit Bureau adapter (#10).

The load-bearing property is the **idempotent hard inquiry**: repeated pulls for
the same application must trigger exactly one underlying inquiry (a second hard
pull would ding the score). Plus typed parsing of score / obligations /
tradelines, and the thin-file shape.
"""
from lending.adapters import (
    BUREAU_PROVIDER,
    HARD_INQUIRY,
    AdapterHarness,
    BureauReport,
    MockAdapter,
    make_mock_bureau_harness,
    pull_bureau,
)
from lending.adapters.bureau import CLEAN_REPORT, THIN_FILE_REPORT


def _harness_with_adapter():
    adapter = MockAdapter(BUREAU_PROVIDER, {HARD_INQUIRY: CLEAN_REPORT})
    harness = AdapterHarness()
    harness.register(adapter)
    return harness, adapter


def test_pull_returns_typed_report_with_score_obligations_tradelines():
    report = pull_bureau(make_mock_bureau_harness(), "app-1")
    assert isinstance(report, BureauReport)
    assert report.score == 700
    assert report.has_record is True
    assert report.total_monthly_obligations == 3_000.0
    assert len(report.tradelines) == 2
    assert report.tradelines[0].lender == "HDFC"
    assert report.tradelines[1].monthly_emi == 3_000.0


def test_hard_inquiry_is_idempotent_per_application():
    harness, adapter = _harness_with_adapter()
    first = pull_bureau(harness, "app-1")
    second = pull_bureau(harness, "app-1")
    assert adapter.execution_count == 1            # exactly one underlying inquiry
    assert first == second


def test_different_application_inquires_again():
    harness, adapter = _harness_with_adapter()
    pull_bureau(harness, "app-1")
    pull_bureau(harness, "app-2")
    assert adapter.execution_count == 2


def test_thin_file_report_shape():
    harness = make_mock_bureau_harness({HARD_INQUIRY: THIN_FILE_REPORT})
    report = pull_bureau(harness, "app-1")
    assert report.has_record is False
    assert report.score is None
    assert report.tradelines == []
