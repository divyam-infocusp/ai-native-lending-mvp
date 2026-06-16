"""
Isolation tests for the adapter harness (#1).

Covers: idempotency (same key → single execution), mock fixtures, unknown
adapter / missing fixture errors, key composition, and cache isolation.
"""
import pytest
from lending.adapters import (
    AdapterError,
    AdapterHarness,
    AdapterRequest,
    InMemoryIdempotencyStore,
    MockAdapter,
    idempotency_key,
)


def make_harness() -> tuple[AdapterHarness, MockAdapter]:
    adapter = MockAdapter("bureau", fixtures={"hard_inquiry": {"score": 720, "report_id": "R1"}})
    harness = AdapterHarness()
    harness.register(adapter)
    return harness, adapter


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def test_mock_returns_registered_fixture():
    harness, _ = make_harness()
    resp = harness.call(AdapterRequest("app1", "bureau", "hard_inquiry"))
    assert resp.data == {"score": 720, "report_id": "R1"}
    assert resp.from_cache is False


def test_mock_fixture_is_copied_not_shared():
    harness, _ = make_harness()
    resp = harness.call(AdapterRequest("app1", "bureau", "hard_inquiry"))
    resp.data["score"] = 999  # mutate the returned data
    # A different application must still get the pristine fixture
    resp2 = harness.call(AdapterRequest("app2", "bureau", "hard_inquiry"))
    assert resp2.data["score"] == 720


# ---------------------------------------------------------------------------
# Idempotency — the load-bearing property
# ---------------------------------------------------------------------------

def test_same_key_executes_once():
    harness, adapter = make_harness()
    req = AdapterRequest("app1", "bureau", "hard_inquiry")
    first = harness.call(req)
    second = harness.call(req)
    assert adapter.execution_count == 1          # side effect ran exactly once
    assert first.from_cache is False
    assert second.from_cache is True
    assert first.data == second.data


def test_different_application_executes_again():
    harness, adapter = make_harness()
    harness.call(AdapterRequest("app1", "bureau", "hard_inquiry"))
    harness.call(AdapterRequest("app2", "bureau", "hard_inquiry"))
    assert adapter.execution_count == 2


def test_different_purpose_executes_again():
    adapter = MockAdapter("bureau", fixtures={
        "hard_inquiry": {"score": 720},
        "soft_pull": {"score": 718},
    })
    harness = AdapterHarness()
    harness.register(adapter)
    harness.call(AdapterRequest("app1", "bureau", "hard_inquiry"))
    harness.call(AdapterRequest("app1", "bureau", "soft_pull"))
    assert adapter.execution_count == 2


def test_idempotency_key_composition():
    key = idempotency_key(AdapterRequest("app1", "bureau", "hard_inquiry"))
    assert key == "app1:bureau:hard_inquiry"


# ---------------------------------------------------------------------------
# Error semantics — never a silent no-op
# ---------------------------------------------------------------------------

def test_unknown_adapter_raises():
    harness, _ = make_harness()
    with pytest.raises(AdapterError, match="unknown adapter provider"):
        harness.call(AdapterRequest("app1", "nonexistent", "x"))


def test_missing_fixture_raises():
    harness, _ = make_harness()
    with pytest.raises(AdapterError, match="no mock fixture"):
        harness.call(AdapterRequest("app1", "bureau", "unregistered_purpose"))


def test_failed_execution_not_cached():
    """A purpose with no fixture raises; the failure must not be cached, so a
    later valid call for a *different* purpose still works and a retry of the
    failing one still raises (not a cached success)."""
    harness, adapter = make_harness()
    with pytest.raises(AdapterError):
        harness.call(AdapterRequest("app1", "bureau", "bad"))
    with pytest.raises(AdapterError):
        harness.call(AdapterRequest("app1", "bureau", "bad"))
    assert adapter.execution_count == 0  # never counted a success


# ---------------------------------------------------------------------------
# Provider routing guard
# ---------------------------------------------------------------------------

def test_shared_store_across_adapters():
    store = InMemoryIdempotencyStore()
    a = MockAdapter("bureau", {"p": {"x": 1}})
    b = MockAdapter("ocr", {"p": {"y": 2}})
    h = AdapterHarness(store=store)
    h.register(a)
    h.register(b)
    # Same application+purpose but different providers → distinct keys, both run
    h.call(AdapterRequest("app1", "bureau", "p"))
    h.call(AdapterRequest("app1", "ocr", "p"))
    assert a.execution_count == 1
    assert b.execution_count == 1
