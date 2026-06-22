"""
e-Sign adapter (#11) — request a signature + handle the completion callback.

On the S0 harness (#1), mock + real-ready, idempotent: requesting a signature
for an application creates exactly one envelope (a retry returns the same one).

The provider calls back when the applicant signs. `parse_signature_callback`
turns that payload into a typed `SignatureEvent`; a completed signature is the
signal that drives `OFFER_GENERATED → OFFER_ACCEPTED` (the state transition
itself lives in the workflow/offer-delivery layer, #23 — this adapter only
exposes the callback mechanism).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import Adapter, AdapterRequest
from .registry import AdapterHarness

ESIGN_PROVIDER = "esign"
ESIGN_REQUEST = "esign_request"

# Envelope / event statuses.
SENT = "sent"
SIGNED = "signed"
DECLINED = "declined"


@dataclass(frozen=True)
class ESignEnvelope:
    application_id: str
    envelope_id: str
    status: str                       # sent | signed | declined
    document_ref: Optional[str]


@dataclass(frozen=True)
class SignatureEvent:
    application_id: str
    envelope_id: str
    signed: bool                      # True only on a completed signature


class MockESignAdapter(Adapter):
    provider = ESIGN_PROVIDER

    def __init__(self) -> None:
        self.execution_count = 0

    def _execute(self, request: AdapterRequest) -> dict:
        self.execution_count += 1
        return {
            "envelope_id": f"env-{request.application_id}",
            "status": SENT,
            "document_ref": (request.payload or {}).get("document_ref"),
        }


def request_signature(
    harness: AdapterHarness,
    application_id: str,
    *,
    document_ref: Optional[str] = None,
) -> ESignEnvelope:
    """Send a document for signature (idempotent — one envelope per application)."""
    resp = harness.call(AdapterRequest(
        application_id, ESIGN_PROVIDER, ESIGN_REQUEST, {"document_ref": document_ref},
    ))
    d = resp.data
    return ESignEnvelope(
        application_id=application_id,
        envelope_id=d["envelope_id"],
        status=d["status"],
        document_ref=d.get("document_ref"),
    )


def parse_signature_callback(payload: dict) -> SignatureEvent:
    """Parse a provider completion callback into a typed event. `signed=True`
    (status == 'signed') is the signal to advance OFFER_GENERATED → OFFER_ACCEPTED."""
    return SignatureEvent(
        application_id=payload["application_id"],
        envelope_id=payload.get("envelope_id", ""),
        signed=payload.get("status") == SIGNED,
    )


def make_mock_esign_harness() -> tuple[AdapterHarness, MockESignAdapter]:
    adapter = MockESignAdapter()
    harness = AdapterHarness()
    harness.register(adapter)
    return harness, adapter
