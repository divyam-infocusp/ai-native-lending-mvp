"""
Runtime settings (#31) — env-driven config for the demo/pilot harness.

All wiring (DB, Temporal address, adapter mode, the pilot feature flag) comes
from the environment, so the same image runs against the compose stack, a local
dev server, or a pilot cluster by changing env only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    temporal_address: str
    adapter_mode: str       # "mock" | "live"
    pilot_enabled: bool     # feature flag gating the live pilot path


class PilotDisabled(Exception):
    """Raised when the pilot path is invoked while the feature flag is off."""


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "sqlite+pysqlite:///./lending.db"),
        temporal_address=os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        adapter_mode=os.environ.get("ADAPTER_MODE", "mock").lower(),
        pilot_enabled=os.environ.get("PILOT_ENABLED", "false").strip().lower() in _TRUTHY,
    )


def require_pilot(settings: Settings) -> None:
    """Gate the pilot path behind the feature flag."""
    if not settings.pilot_enabled:
        raise PilotDisabled("pilot path is disabled; set PILOT_ENABLED=true to enable")
