"""
Isolation tests for the Audit & Explainability store (#6 / §9.1).

Covers: append order, ordering across interleaved appends, unknown-id → empty,
event immutability, and the absence of any update/delete path.
"""
import pytest
from pydantic import ValidationError

from lending.audit import AuditEvent, AuditStore, EventType
from lending.los import make_engine


@pytest.fixture
def store() -> AuditStore:
    return AuditStore(make_engine())


# ---------------------------------------------------------------------------
# Append + reconstruct
# ---------------------------------------------------------------------------

def test_append_returns_event_with_seq(store):
    evt = store.append("a1", EventType.INPUT, {"income": 50000}, actor="intake")
    assert evt.application_id == "a1"
    assert evt.event_type == "input"
    assert evt.payload == {"income": 50000}
    assert evt.actor == "intake"
    assert isinstance(evt.seq, int)


def test_reconstruct_returns_in_append_order(store):
    store.append("a1", EventType.INPUT, {"step": 1})
    store.append("a1", EventType.TOOL_CALL, {"step": 2})
    store.append("a1", EventType.RULE_FIRED, {"step": 3})
    trail = store.reconstruct("a1")
    assert [e.payload["step"] for e in trail] == [1, 2, 3]
    assert [e.event_type for e in trail] == ["input", "tool_call", "rule_fired"]


def test_reconstruct_unknown_id_returns_empty_not_error(store):
    assert store.reconstruct("ghost-id") == []


def test_free_form_event_type_allowed(store):
    evt = store.append("a1", "custom_event", {"x": 1})
    assert evt.event_type == "custom_event"


# ---------------------------------------------------------------------------
# Ordering across interleaved appends (different applications)
# ---------------------------------------------------------------------------

def test_ordering_preserved_across_interleaved_appends(store):
    store.append("a1", EventType.INPUT, {"n": "a1-1"})
    store.append("a2", EventType.INPUT, {"n": "a2-1"})
    store.append("a1", EventType.TOOL_CALL, {"n": "a1-2"})
    store.append("a2", EventType.TOOL_CALL, {"n": "a2-2"})
    store.append("a1", EventType.DECISION, {"n": "a1-3"})

    a1 = store.reconstruct("a1")
    a2 = store.reconstruct("a2")
    assert [e.payload["n"] for e in a1] == ["a1-1", "a1-2", "a1-3"]
    assert [e.payload["n"] for e in a2] == ["a2-1", "a2-2"]
    # Global seq strictly increases in append order
    assert a1[0].seq < a2[0].seq < a1[1].seq < a2[1].seq < a1[2].seq


# ---------------------------------------------------------------------------
# Immutability — events frozen, no update/delete path
# ---------------------------------------------------------------------------

def test_event_is_frozen(store):
    evt = store.append("a1", EventType.INPUT, {"x": 1})
    with pytest.raises(ValidationError):
        evt.payload = {"x": 999}
    with pytest.raises(ValidationError):
        evt.seq = 0


def test_store_has_no_update_or_delete_path(store):
    # The append-only contract: no mutation API exists.
    for forbidden in ("update", "delete", "remove", "edit", "set"):
        assert not hasattr(store, forbidden), f"store unexpectedly exposes {forbidden!r}"


def test_mutating_a_reconstructed_event_does_not_affect_store(store):
    store.append("a1", EventType.INPUT, {"x": 1})
    trail = store.reconstruct("a1")
    trail[0].payload["x"] = 999  # mutate the in-memory dict of the returned copy
    # DB is the source of truth; a fresh reconstruct is unchanged
    fresh = store.reconstruct("a1")
    assert fresh[0].payload == {"x": 1}


def test_append_never_overwrites(store):
    e1 = store.append("a1", EventType.INPUT, {"x": 1})
    e2 = store.append("a1", EventType.INPUT, {"x": 1})  # identical content
    assert e1.seq != e2.seq
    assert e1.event_id != e2.event_id
    assert len(store.reconstruct("a1")) == 2
