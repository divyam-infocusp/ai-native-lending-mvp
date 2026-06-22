"""
Loan Origination System (LOS) application aggregate — system-of-record (#2).

The post-§16 schema, as Pydantic models. Notable design points carried from
the design review:
  - Two-layer consent (§16.6): Layer 1 customer authorizations (no timer) +
    Layer 2 per-pull artifacts minted fresh at pull time.
  - KYC carries grounded field_confidence + risk_flags (§16.4).
  - Decision records its source (§16.10): "engine" or "underwriter:<id>".
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from lending.governance.models import VersionSet


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ApplicationStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    DECIDED = "decided"
    EXCEPTION = "exception"


class ConsentStatus(str, Enum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class KycStatus(str, Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class Disposition(str, Enum):
    PENDING = "pending"
    APPROVE = "approve"
    DECLINE = "decline"
    REFER = "refer"


# ---------------------------------------------------------------------------
# Consent (§16.6) — two layers
# ---------------------------------------------------------------------------

class ConsentAuthorization(BaseModel):
    """Layer 1: customer authorization for a purpose. No expiry timer."""
    purpose: str
    status: ConsentStatus = ConsentStatus.ACTIVE
    captured_at: datetime = Field(default_factory=_utcnow)


class ConsentArtifact(BaseModel):
    """Layer 2: per-pull artifact minted fresh at the moment of a data pull."""
    pull_purpose: str
    minted_at: datetime = Field(default_factory=_utcnow)
    reference: str


class Consent(BaseModel):
    authorizations: list[ConsentAuthorization] = Field(default_factory=list)
    artifacts: list[ConsentArtifact] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# KYC (§16.4)
# ---------------------------------------------------------------------------

class FieldConfidence(BaseModel):
    field_name: str
    confidence: float
    risk_flags: list[str] = Field(default_factory=list)


class Kyc(BaseModel):
    status: KycStatus = KycStatus.PENDING
    field_confidence: list[FieldConfidence] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Decision (§16.10)
# ---------------------------------------------------------------------------

class Decision(BaseModel):
    disposition: Disposition = Disposition.PENDING
    source: str = "engine"  # "engine" | "underwriter:<id>"
    reason_codes: list[str] = Field(default_factory=list)
    rules_version: Optional[str] = None
    scorecard_version: Optional[str] = None
    score: Optional[int] = None
    band: Optional[str] = None
    version_set: Optional[VersionSet] = None  # full pinned version stamp (§9.4, #7)
    explanation: Optional[str] = None         # rendered adverse-action text, frozen at decision time (§9.1)


# ---------------------------------------------------------------------------
# Applicant + features
# ---------------------------------------------------------------------------

class Applicant(BaseModel):
    full_name: str
    pan: Optional[str] = None
    aadhaar: Optional[str] = None
    date_of_birth: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    current_address: Optional[str] = None


# ---------------------------------------------------------------------------
# Aggregate root
# ---------------------------------------------------------------------------

class Application(BaseModel):
    application_id: str = Field(default_factory=_new_id)
    status: ApplicationStatus = ApplicationStatus.CREATED
    workflow_state: Optional[str] = None  # fine-grained §4 state, driven by the workflow (#13)
    owner_user_id: Optional[str] = None   # the applicant who owns this application (#38)
    applicant: Applicant
    features: dict = Field(default_factory=dict)
    consent: Consent = Field(default_factory=Consent)
    kyc: Kyc = Field(default_factory=Kyc)
    decision: Optional[Decision] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ApplicationCreate(BaseModel):
    """Intake payload for POST /applications. Server assigns id/timestamps."""
    applicant: Applicant
    features: dict = Field(default_factory=dict)
    consent: Consent = Field(default_factory=Consent)
