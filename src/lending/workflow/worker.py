"""
Worker entrypoint (#13) — connects to a Temporal server and runs the workflow.

Connection and DB are config-driven (env vars) so the same code runs against the
in-process test server (tests), a local dev server (demo), or a real cluster
(pilot) — only the TEMPORAL_ADDRESS / DATABASE_URL change. The production-server
deployment itself is tracked in #31.
"""
from __future__ import annotations

import asyncio
import os

from temporalio.client import Client
from temporalio.worker import Worker

from lending.audit import AuditStore
from lending.los import ApplicationRepository, make_engine

from .activities import OriginationActivities
from .workflow import TASK_QUEUE, LoanOriginationWorkflow


def build_activities(database_url: str) -> OriginationActivities:
    engine = make_engine(database_url)
    return OriginationActivities(ApplicationRepository(engine), AuditStore(engine))


async def main() -> None:
    temporal_address = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
    database_url = os.environ.get("DATABASE_URL", "sqlite+pysqlite:///./lending.db")

    activities = build_activities(database_url)
    client = await Client.connect(temporal_address)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[LoanOriginationWorkflow],
        activities=[activities.advance, activities.decide],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
