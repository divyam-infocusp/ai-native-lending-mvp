"""
Audit & Explainability store (#6, design §9.1).

Append-only event stream, one logical stream per application. The store exposes
exactly two operations — append() and reconstruct() — and deliberately offers
**no update or delete path**: an audit log you can edit is worthless as evidence.

`seq` is a global autoincrement key, so reconstruct() returns an application's
events in true append order even when appends from different applications are
interleaved. Backed by SQLAlchemy (JSONB on Postgres, JSON on SQLite).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    select,
)
from sqlalchemy.engine import Engine

from .models import AuditEvent, EventType

_metadata = MetaData()

audit_events_table = Table(
    "audit_events",
    _metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),  # global append order
    Column("event_id", String, nullable=False, unique=True),
    Column("application_id", String, nullable=False, index=True),
    Column("event_type", String, nullable=False),
    Column("actor", String, nullable=True),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_type(event_type: EventType | str) -> str:
    return event_type.value if isinstance(event_type, EventType) else str(event_type)


class AuditStore:
    """Append-only. No update/delete methods exist by design."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        _metadata.create_all(engine)

    def append(
        self,
        application_id: str,
        event_type: EventType | str,
        payload: dict,
        actor: str | None = None,
    ) -> AuditEvent:
        event_id = uuid4().hex
        created_at = _utcnow()
        values = {
            "event_id": event_id,
            "application_id": application_id,
            "event_type": _normalize_type(event_type),
            "actor": actor,
            "payload": payload,
            "created_at": created_at,
        }
        with self._engine.begin() as conn:
            result = conn.execute(audit_events_table.insert().values(**values))
            seq = result.inserted_primary_key[0]
        return AuditEvent(seq=seq, **values)

    def reconstruct(self, application_id: str) -> list[AuditEvent]:
        """Ordered event trail for an application. Unknown id → empty list."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(audit_events_table)
                .where(audit_events_table.c.application_id == application_id)
                .order_by(audit_events_table.c.seq.asc())
            ).all()
        return [
            AuditEvent(
                seq=r.seq,
                event_id=r.event_id,
                application_id=r.application_id,
                event_type=r.event_type,
                payload=r.payload,
                created_at=r.created_at,
                actor=r.actor,
            )
            for r in rows
        ]
