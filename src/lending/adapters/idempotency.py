"""
Idempotency store interface + in-memory implementation (#1).

The interface is what the harness depends on. The in-memory impl is enough for
tests and single-process mock runs; a persistent (DB-backed) impl lands with the
LOS/audit store (#2/#6) so idempotency survives across workers and restarts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IdempotencyStore(ABC):
    @abstractmethod
    def get(self, key: str) -> dict | None:
        """Return the stored result for key, or None if not seen."""

    @abstractmethod
    def put(self, key: str, value: dict) -> None:
        """Record the result for key (first successful execution only)."""


class InMemoryIdempotencyStore(IdempotencyStore):
    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self._store.get(key)

    def put(self, key: str, value: dict) -> None:
        # First write wins; an existing key is never re-executed so this is
        # defensive only.
        self._store.setdefault(key, value)
