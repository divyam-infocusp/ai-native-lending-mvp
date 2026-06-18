"""
Checkpointer factory (#16, §16.5) — the durability backing for the agent graph.

`retry = reload` only survives a real worker restart if the checkpoint lives
outside the process. So agents in the live stack use a **Postgres** checkpointer;
tests and local runs fall back to in-memory.

The same DATABASE_URL the LOS uses is reused (converted to a psycopg conninfo).
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


def _to_conninfo(database_url: str) -> str:
    # SQLAlchemy-style → psycopg conninfo: postgresql+psycopg://… → postgresql://…
    return (
        database_url
        .replace("postgresql+psycopg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


def make_checkpointer(database_url: str | None = None):
    """Return a durable Postgres checkpointer for a Postgres URL, else MemorySaver.

    For Postgres, opens a connection pool and runs the one-time schema setup.
    The caller owns the pool's lifetime (it lives as long as the worker).
    """
    if not database_url or database_url.startswith("sqlite"):
        return MemorySaver()

    # Imported lazily so non-Postgres runs don't require the pool/driver.
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        conninfo=_to_conninfo(database_url),
        max_size=10,
        open=True,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    saver = PostgresSaver(pool)
    saver.setup()  # idempotent: creates the checkpoint tables if absent
    return saver
