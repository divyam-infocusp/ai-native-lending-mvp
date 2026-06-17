"""
Tests for the governance / versioning scaffold (#7 / §9.4).

Covers: active version set resolves + validates, missing-stamp rejection (the
gate), unknown-version rejection, and that a decision record can carry the
complete pinned version set and round-trips through persistence.
"""
import pytest
from pydantic import ValidationError

from lending.governance import (
    IncompleteVersionSet,
    UnknownVersion,
    VersionSet,
    active_version_set,
    validate_decision_versioning,
    validate_version_set,
)
from lending.los import (
    Applicant,
    Application,
    ApplicationRepository,
    Decision,
    make_engine,
)


# ---------------------------------------------------------------------------
# Active version set
# ---------------------------------------------------------------------------

def test_active_version_set_is_complete_and_valid():
    vs = active_version_set()
    # all six stamps present
    assert vs.rules and vs.scorecard and vs.pricing and vs.confidence
    assert vs.model_id and vs.prompt_version
    # and it validates clean (versions exist in their catalogs)
    validate_version_set(vs)  # must not raise


# ---------------------------------------------------------------------------
# Missing stamp → fails validation (the gate)
# ---------------------------------------------------------------------------

def test_constructing_with_missing_field_raises():
    with pytest.raises(ValidationError):
        VersionSet(rules="v1", scorecard="v1", pricing="v1", confidence="v1", model_id="m")
        # prompt_version omitted


def test_empty_stamp_rejected():
    vs = VersionSet(rules="v1", scorecard="v1", pricing="v1", confidence="v1",
                    model_id="claude-sonnet-4-6", prompt_version="")
    with pytest.raises(IncompleteVersionSet):
        validate_version_set(vs)


def test_decision_without_version_set_rejected():
    with pytest.raises(IncompleteVersionSet):
        validate_decision_versioning(None)


def test_decision_with_full_version_set_validates():
    validate_decision_versioning(active_version_set())  # must not raise


# ---------------------------------------------------------------------------
# Unknown version → rejected
# ---------------------------------------------------------------------------

def test_unknown_policy_version_rejected():
    vs = VersionSet(rules="v99", scorecard="v1", pricing="v1", confidence="v1",
                    model_id="claude-sonnet-4-6", prompt_version="explain-v1")
    with pytest.raises(UnknownVersion):
        validate_version_set(vs)


# ---------------------------------------------------------------------------
# A decision record carries the complete pinned version set (and persists it)
# ---------------------------------------------------------------------------

def test_decision_carries_version_set_and_round_trips():
    repo = ApplicationRepository(make_engine())
    vs = active_version_set()
    app = Application(
        applicant=Applicant(full_name="Priya Sharma"),
        decision=Decision(disposition="approve", source="engine",
                          reason_codes=["CLEAN"], version_set=vs),
    )
    repo.save(app)
    loaded = repo.get(app.application_id)
    assert loaded.decision.version_set == vs
    validate_decision_versioning(loaded.decision.version_set)  # still valid after reload
