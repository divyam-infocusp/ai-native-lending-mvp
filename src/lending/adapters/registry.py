"""
Adapter registry + harness (#1).

The harness ties a registry of adapters (keyed by provider) to a shared
idempotency store and dispatches calls. This is the single entry point the rest
of the system uses to reach any external integration.
"""
from __future__ import annotations

from .base import Adapter, AdapterError, AdapterRequest, AdapterResponse
from .idempotency import IdempotencyStore, InMemoryIdempotencyStore


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, adapter: Adapter) -> None:
        self._adapters[adapter.provider] = adapter

    def get(self, provider: str) -> Adapter:
        if provider not in self._adapters:
            raise AdapterError(f"unknown adapter provider: {provider!r}")
        return self._adapters[provider]


class AdapterHarness:
    def __init__(self, store: IdempotencyStore | None = None) -> None:
        self.registry = AdapterRegistry()
        self.store = store or InMemoryIdempotencyStore()

    def register(self, adapter: Adapter) -> None:
        self.registry.register(adapter)

    def call(self, request: AdapterRequest) -> AdapterResponse:
        adapter = self.registry.get(request.provider)
        return adapter.call(request, self.store)
