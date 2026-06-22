"""
Tests for the two-layer consent gate (#8, §16.6).

Every block condition (no L1, withdrawn L1, wrong purpose, absent L2, stale L2)
and the happy path (allows + mints L2, both artifact ids audited).
"""
from datetime import datetime, timedelta, timezone

import pytest

from lending.audit import AuditStore
from lending.consent import (
    ConsentError,
    capture_authorization,
    enforce_consent,
    verify_artifact_fresh,
    withdraw_authorization,
)
from lending.los import Applicant, Application, ApplicationRepository, make_engine

PURPOSE = "bureau_pull"


def _setup():
    engine = make_engine()
    repo = ApplicationRepository(engine)
    audit = AuditStore(engine)
    app = Application(applicant=Applicant(full_name="Priya Sharma"))
    repo.save(app)
    return repo, audit, app


# ---------------------------------------------------------------------------
# Layer-1 blocks
# ---------------------------------------------------------------------------

def test_blocks_when_no_layer1():
    _, audit, app = _setup()
    with pytest.raises(ConsentError, match="no Layer-1 authorization for purpose"):
        enforce_consent(app, PURPOSE, audit)


def test_blocks_on_withdrawn_layer1():
    _, audit, app = _setup()
    capture_authorization(app, PURPOSE)
    withdraw_authorization(app, PURPOSE)
    with pytest.raises(ConsentError, match="withdrawn"):
        enforce_consent(app, PURPOSE, audit)


def test_blocks_on_wrong_purpose():
    _, audit, app = _setup()
    capture_authorization(app, "marketing")          # a grant, but not for the pull
    with pytest.raises(ConsentError, match="wrong purpose"):
        enforce_consent(app, PURPOSE, audit)


# ---------------------------------------------------------------------------
# Layer-2 blocks
# ---------------------------------------------------------------------------

def test_blocks_on_absent_layer2():
    _, audit, app = _setup()
    capture_authorization(app, PURPOSE)
    with pytest.raises(ConsentError, match="no Layer-2 artifact"):
        verify_artifact_fresh(app, "does-not-exist", PURPOSE)


def test_blocks_on_stale_layer2():
    _, audit, app = _setup()
    capture_authorization(app, PURPOSE)
    # Mint an artifact 'now', then verify far in the future → stale.
    minted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    artifact = enforce_consent(app, PURPOSE, audit, now=minted_at)
    much_later = minted_at + timedelta(seconds=301)
    with pytest.raises(ConsentError, match="stale"):
        verify_artifact_fresh(app, artifact.reference, PURPOSE, now=much_later)


# ---------------------------------------------------------------------------
# Happy path — allows, mints L2, audits both ids, fresh artifact verifies
# ---------------------------------------------------------------------------

def test_happy_path_allows_and_mints_layer2():
    _, audit, app = _setup()
    capture_authorization(app, PURPOSE, audit)

    minted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    artifact = enforce_consent(app, PURPOSE, audit, now=minted_at)

    # Layer-2 minted + recorded on the aggregate
    assert artifact.pull_purpose == PURPOSE
    assert artifact.reference
    assert app.consent.artifacts[-1].reference == artifact.reference

    # A fresh artifact verifies for the pull
    soon = minted_at + timedelta(seconds=60)
    assert verify_artifact_fresh(app, artifact.reference, PURPOSE, now=soon) is artifact

    # Both artifact ids logged to audit (the L1 purpose grant + the L2 reference)
    mint_events = [e for e in audit.reconstruct(app.application_id)
                   if e.event_type == "consent" and e.payload.get("layer") == 2]
    assert len(mint_events) == 1
    assert mint_events[0].payload["layer1_purpose"] == PURPOSE
    assert mint_events[0].payload["layer2_reference"] == artifact.reference
