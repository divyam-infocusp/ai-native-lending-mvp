"""
Two-layer consent capture + gate (#8, §16.6).

Two layers protect every external data pull (e.g. a bureau hard inquiry):

  - **Layer 1 — customer authorization.** A standing grant for a purpose
    (no timer). Captured once; can be withdrawn.
  - **Layer 2 — per-pull artifact.** A fresh token minted at the *moment* of a
    pull, valid only briefly. Forces a contemporaneous record per pull rather
    than relying on the standing grant alone.

The gate a caller (e.g. the Underwriting Agent #20) runs before a pull:
  1. `enforce_consent()` — checks Layer-1 (blocks on missing / withdrawn /
     wrong-purpose), then mints a fresh Layer-2 artifact and audits BOTH
     artifact ids (the Layer-1 purpose grant + the Layer-2 reference).
  2. `verify_artifact_fresh()` — guards the actual pull: a stale or absent
     Layer-2 artifact is rejected, so an old token can't authorize a new pull.

Both raise `ConsentError` on a block — never a silent allow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from lending.audit import AuditStore
from lending.los.schema import ConsentArtifact, ConsentAuthorization, ConsentStatus
from lending.policy import CONSENT_POLICY

CONSENT_EVENT = "consent"


class ConsentError(Exception):
    """Raised when the consent gate blocks a pull (never a silent no-op)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _l2_freshness(policy_version: str) -> int:
    if policy_version not in CONSENT_POLICY:
        raise ValueError(f"Unknown policy_version: {policy_version!r}")
    return CONSENT_POLICY[policy_version]["l2_freshness_seconds"]


# ---------------------------------------------------------------------------
# Layer-1 capture / withdrawal
# ---------------------------------------------------------------------------

def capture_authorization(application, purpose: str, audit: Optional[AuditStore] = None) -> ConsentAuthorization:
    """Capture (or re-activate) a Layer-1 customer authorization for a purpose."""
    auth = _find_authorization(application, purpose)
    if auth is None:
        auth = ConsentAuthorization(purpose=purpose, status=ConsentStatus.ACTIVE)
        application.consent.authorizations.append(auth)
    else:
        auth.status = ConsentStatus.ACTIVE
    if audit is not None:
        audit.append(
            application.application_id, CONSENT_EVENT,
            {"layer": 1, "action": "captured", "purpose": purpose},
            actor="applicant",
        )
    return auth


def withdraw_authorization(application, purpose: str) -> None:
    """Withdraw a Layer-1 authorization (the gate will then block this purpose)."""
    auth = _find_authorization(application, purpose)
    if auth is not None:
        auth.status = ConsentStatus.WITHDRAWN


def _find_authorization(application, purpose: str) -> Optional[ConsentAuthorization]:
    for auth in application.consent.authorizations:
        if auth.purpose == purpose:
            return auth
    return None


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def enforce_consent(
    application,
    purpose: str,
    audit: AuditStore,
    *,
    now: Optional[datetime] = None,
) -> ConsentArtifact:
    """Layer-1 gate + Layer-2 mint. Blocks (ConsentError) on missing / withdrawn /
    wrong-purpose Layer-1; otherwise mints a fresh Layer-2 artifact, audits both
    artifact ids, and returns the minted artifact."""
    now = now or _utcnow()
    auth = _find_authorization(application, purpose)
    if auth is None:
        # Distinguish "wrong purpose" (grants exist, none match) from "none at all".
        if application.consent.authorizations:
            raise ConsentError(f"no Layer-1 authorization matching purpose {purpose!r} (wrong purpose)")
        raise ConsentError(f"no Layer-1 authorization for purpose {purpose!r}")
    if auth.status != ConsentStatus.ACTIVE:
        raise ConsentError(f"Layer-1 authorization for {purpose!r} is {auth.status.value}")

    artifact = ConsentArtifact(pull_purpose=purpose, minted_at=now, reference=uuid4().hex)
    application.consent.artifacts.append(artifact)

    audit.append(
        application.application_id, CONSENT_EVENT,
        {
            "layer": 2, "action": "minted", "purpose": purpose,
            "layer1_purpose": auth.purpose,            # the Layer-1 grant honored
            "layer2_reference": artifact.reference,    # the Layer-2 artifact id
        },
        actor="system",
    )
    return artifact


def verify_artifact_fresh(
    application,
    reference: str,
    purpose: str,
    *,
    now: Optional[datetime] = None,
    policy_version: str = "v1",
) -> ConsentArtifact:
    """Guard the actual pull: the Layer-2 artifact must exist, match the purpose,
    and be fresh (minted within the policy window). Blocks (ConsentError) on
    absent / wrong-purpose / stale."""
    now = now or _utcnow()
    ttl = _l2_freshness(policy_version)
    artifact = next(
        (a for a in application.consent.artifacts
         if a.reference == reference and a.pull_purpose == purpose),
        None,
    )
    if artifact is None:
        raise ConsentError(f"no Layer-2 artifact {reference!r} for purpose {purpose!r}")
    if now - artifact.minted_at > timedelta(seconds=ttl):
        raise ConsentError(f"Layer-2 artifact {reference!r} is stale (older than {ttl}s)")
    return artifact
