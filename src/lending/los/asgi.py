"""
ASGI entrypoint (#31) — the API the container serves.

Builds the FastAPI app against the configured database (Postgres in the compose
stack, SQLite by default). Run with: `uvicorn lending.los.asgi:app`.
"""
from __future__ import annotations

from lending.settings import load_settings

from .api import create_app
from .repository import ApplicationRepository, make_engine

_settings = load_settings()
app = create_app(ApplicationRepository(make_engine(_settings.database_url)))
