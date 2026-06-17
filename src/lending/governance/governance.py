"""
Governance / versioning scaffold (#7, §9.4).

Two jobs:
  1. Resolve the currently-active version set (what a fresh decision is stamped with).
  2. Validate a version set / a decision's stamp — rejecting anything incomplete
     or referencing a version that doesn't exist in the policy catalogs. This is
     the "no decision you couldn't later reproduce" gate.

The active versions are pinned here for the MVP. A fuller governance system
would resolve them from a governed registry with change audit and an editing
surface (the broader scope still tracked on this issue).
"""
from __future__ import annotations

from lending.policy import (
    CONFIDENCE_POLICY,
    PRICING_POLICY,
    RULES_POLICY,
    SCORECARD_POLICY,
)

from .models import VersionSet


class IncompleteVersionSet(Exception):
    """A decision/version set is missing one or more required stamps."""


class UnknownVersion(Exception):
    """A stamped version does not exist in its policy catalog."""


# Currently-active, pinned versions. The decision-of-record is stamped with these.
_ACTIVE: dict[str, str] = {
    "rules": "v1",
    "scorecard": "v1",
    "pricing": "v1",
    "confidence": "v1",
    "model_id": "claude-sonnet-4-6",
    "prompt_version": "explain-v1",
}

# Policy-domain → its versioned catalog, for existence checks.
_CATALOGS = {
    "rules": RULES_POLICY,
    "scorecard": SCORECARD_POLICY,
    "pricing": PRICING_POLICY,
    "confidence": CONFIDENCE_POLICY,
}

_REQUIRED_FIELDS = ("rules", "scorecard", "pricing", "confidence", "model_id", "prompt_version")


def active_version_set() -> VersionSet:
    """The version set a new decision should be stamped with."""
    return VersionSet(**_ACTIVE)


def validate_version_set(version_set: VersionSet) -> None:
    """Raise if any stamp is empty or names a version absent from its catalog."""
    for field in _REQUIRED_FIELDS:
        if not getattr(version_set, field):
            raise IncompleteVersionSet(f"missing version stamp: {field}")
    for domain, catalog in _CATALOGS.items():
        version = getattr(version_set, domain)
        if version not in catalog:
            raise UnknownVersion(f"{domain} version {version!r} is not in its catalog")


def validate_decision_versioning(version_set: VersionSet | None) -> None:
    """Gate for the decision-of-record: a decision must carry a complete,
    catalog-valid version set, or it is rejected."""
    if version_set is None:
        raise IncompleteVersionSet("decision is missing its version set")
    validate_version_set(version_set)
