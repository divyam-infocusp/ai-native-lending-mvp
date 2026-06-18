"""
Tests for the runtime harness (#31) — the parts that are unit-testable without
Docker: env-driven settings, the pilot feature-flag gate, and the ASGI app /
health endpoint. The full-stack `docker compose up` smoke test is run separately
(see the README "Run the demo" path).
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from lending.settings import PilotDisabled, Settings, load_settings, require_pilot


# ---------------------------------------------------------------------------
# Settings (env-driven)
# ---------------------------------------------------------------------------

def test_defaults(monkeypatch):
    for var in ("DATABASE_URL", "TEMPORAL_ADDRESS", "ADAPTER_MODE", "PILOT_ENABLED"):
        monkeypatch.delenv(var, raising=False)
    s = load_settings()
    assert s.database_url.startswith("sqlite")
    assert s.temporal_address == "localhost:7233"
    assert s.adapter_mode == "mock"      # safe default: no real external calls
    assert s.pilot_enabled is False      # safe default: pilot off


def test_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/lending")
    monkeypatch.setenv("TEMPORAL_ADDRESS", "temporal:7233")
    monkeypatch.setenv("ADAPTER_MODE", "MOCK")
    monkeypatch.setenv("PILOT_ENABLED", "true")
    s = load_settings()
    assert s.database_url.endswith("/lending")
    assert s.temporal_address == "temporal:7233"
    assert s.adapter_mode == "mock"      # normalized lower-case
    assert s.pilot_enabled is True


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True), ("on", True), ("TRUE", True),
    ("false", False), ("0", False), ("", False), ("nope", False),
])
def test_pilot_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("PILOT_ENABLED", raw)
    assert load_settings().pilot_enabled is expected


# ---------------------------------------------------------------------------
# Feature-flag gate
# ---------------------------------------------------------------------------

def test_require_pilot_raises_when_off():
    s = Settings(database_url="x", temporal_address="y", adapter_mode="mock", pilot_enabled=False)
    with pytest.raises(PilotDisabled):
        require_pilot(s)


def test_require_pilot_passes_when_on():
    s = Settings(database_url="x", temporal_address="y", adapter_mode="mock", pilot_enabled=True)
    require_pilot(s)  # must not raise


# ---------------------------------------------------------------------------
# ASGI app + health
# ---------------------------------------------------------------------------

def test_asgi_app_builds_and_health_ok(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    import lending.los.asgi as asgi
    importlib.reload(asgi)  # rebuild the app against the patched env
    client = TestClient(asgi.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
