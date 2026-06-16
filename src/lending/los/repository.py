"""
Application repository (#2) — persistence for the LOS aggregate.

The aggregate is stored as a single JSON document keyed by application_id. This
maps to JSONB on Postgres (the production target) and to JSON on SQLite (used in
tests), so the same code path is exercised in both. The full Pydantic schema is
the contract; the DB stores its serialized form and round-trips it back.
"""
from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from .schema import Application

_metadata = MetaData()

applications_table = Table(
    "applications",
    _metadata,
    Column("application_id", String, primary_key=True),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


class ApplicationRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        _metadata.create_all(engine)

    def save(self, application: Application) -> Application:
        payload = application.model_dump(mode="json")
        row = {
            "application_id": application.application_id,
            "payload": payload,
            "created_at": application.created_at,
            "updated_at": application.updated_at,
        }
        with self._engine.begin() as conn:
            exists = conn.execute(
                select(applications_table.c.application_id).where(
                    applications_table.c.application_id == application.application_id
                )
            ).first()
            if exists:
                conn.execute(
                    applications_table.update()
                    .where(applications_table.c.application_id == application.application_id)
                    .values(payload=payload, updated_at=application.updated_at)
                )
            else:
                conn.execute(applications_table.insert().values(**row))
        return application

    def get(self, application_id: str) -> Application | None:
        with self._engine.connect() as conn:
            result = conn.execute(
                select(applications_table.c.payload).where(
                    applications_table.c.application_id == application_id
                )
            ).first()
        if result is None:
            return None
        return Application.model_validate(result[0])


def make_engine(url: str = "sqlite+pysqlite:///:memory:") -> Engine:
    """Create an engine. Default is in-memory SQLite (tests); pass a Postgres URL
    in production. check_same_thread off so a single in-memory DB is shared
    across FastAPI's threadpool within a process."""
    if url.startswith("sqlite"):
        # StaticPool keeps a single connection so an in-memory DB is shared
        # across the app's threadpool within a process.
        return create_engine(
            url, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    return create_engine(url)
