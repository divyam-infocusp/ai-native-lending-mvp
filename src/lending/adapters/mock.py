"""
Mock adapter (#1) — deterministic canned responses for local/dev/test runs.

Fixtures are keyed by purpose. A request for an unregistered purpose raises
AdapterError (no silent no-op). execution_count exposes how many times the
underlying "side effect" actually ran — used to prove idempotency.
"""
from __future__ import annotations

from copy import deepcopy

from .base import Adapter, AdapterError, AdapterRequest


class MockAdapter(Adapter):
    def __init__(self, provider: str, fixtures: dict[str, dict]) -> None:
        self.provider = provider
        self._fixtures = fixtures
        self.execution_count = 0

    def _execute(self, request: AdapterRequest) -> dict:
        if request.purpose not in self._fixtures:
            raise AdapterError(
                f"no mock fixture for {self.provider!r}/{request.purpose!r}"
            )
        self.execution_count += 1
        # Return a copy so callers can't mutate the canned fixture.
        return deepcopy(self._fixtures[request.purpose])
