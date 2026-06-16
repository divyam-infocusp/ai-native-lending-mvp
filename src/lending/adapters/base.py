"""
Adapter harness — common interface for every external integration (#1).

Two foundations every adapter (OCR, KYC, bureau, e-sign, notifications) gets
for free:
  - a single call shape (AdapterRequest → AdapterResponse);
  - idempotency keyed by application_id + provider + purpose, so a retry never
    double-executes a side-effecting call (e.g. a second bureau hard inquiry).

The *logic* of each integration lives in its own Adapter subclass's _execute;
this module owns dispatch, idempotency, and error semantics only.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .idempotency import IdempotencyStore


class AdapterError(Exception):
    """Raised for unknown/unconfigured adapters or missing fixtures — never a
    silent no-op."""


@dataclass(frozen=True)
class AdapterRequest:
    application_id: str
    provider: str
    purpose: str
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterResponse:
    provider: str
    purpose: str
    idempotency_key: str
    data: dict
    from_cache: bool  # True when served from the idempotency store (no re-execution)


def idempotency_key(request: AdapterRequest) -> str:
    """The contractual key: application_id + provider + purpose."""
    return f"{request.application_id}:{request.provider}:{request.purpose}"


class Adapter(ABC):
    """Base for all adapters. Subclasses set `provider` and implement _execute."""

    provider: str

    @abstractmethod
    def _execute(self, request: AdapterRequest) -> dict:
        """Perform the actual (possibly side-effecting) call and return its data."""

    def call(self, request: AdapterRequest, store: IdempotencyStore) -> AdapterResponse:
        if request.provider != self.provider:
            raise AdapterError(
                f"request.provider {request.provider!r} routed to adapter {self.provider!r}"
            )

        key = idempotency_key(request)
        cached = store.get(key)
        if cached is not None:
            return AdapterResponse(self.provider, request.purpose, key, cached, from_cache=True)

        data = self._execute(request)          # side effect happens at most once per key
        store.put(key, data)
        return AdapterResponse(self.provider, request.purpose, key, data, from_cache=False)
