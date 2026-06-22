"""
Worker entrypoint (#13) — connects to a Temporal server and runs the workflow.

Connection and DB are config-driven (env vars) so the same code runs against the
in-process test server (tests), a local dev server (demo), or a real cluster
(pilot) — only the TEMPORAL_ADDRESS / DATABASE_URL change. The production-server
deployment itself is tracked in #31.
"""
from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from lending.audit import AuditStore
from lending.los import ApplicationRepository, make_engine
from lending.settings import load_settings

from .activities import OriginationActivities
from .workflow import TASK_QUEUE, LoanOriginationWorkflow


def build_doc_extractor(adapter_mode: str):
    """Document extractor (OCR/KYC) for the Document Intelligence Agent (#19).

    `mock` → a placeholder mock OCR harness so the demo runs end-to-end; `live` →
    the real OCR/KYC adapters, which arrive in #9 (not built yet)."""
    from lending.agents import make_ocr_extractor

    if adapter_mode == "live":
        raise NotImplementedError("live OCR/KYC adapter not built yet (#9); use ADAPTER_MODE=mock")
    from lending.adapters.ocr_mock import make_mock_ocr_harness

    return make_ocr_extractor(make_mock_ocr_harness())


def build_activities(database_url: str, adapter_mode: str = "mock") -> OriginationActivities:
    engine = make_engine(database_url)
    return OriginationActivities(
        ApplicationRepository(engine),
        AuditStore(engine),
        doc_extract=build_doc_extractor(adapter_mode),
    )


async def _connect_with_retry(address: str, attempts: int = 30, delay: float = 2.0) -> Client:
    """Temporal may not be ready the instant the worker boots (compose start
    order), so retry the initial connection before giving up."""
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            return await Client.connect(address)
        except Exception as err:  # noqa: BLE001 - retry any connection failure
            last_err = err
            await asyncio.sleep(delay)
    raise RuntimeError(f"could not connect to Temporal at {address!r}") from last_err


async def main() -> None:
    settings = load_settings()
    activities = build_activities(settings.database_url, settings.adapter_mode)
    client = await _connect_with_retry(settings.temporal_address)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[LoanOriginationWorkflow],
        activities=[
            activities.advance,
            activities.decide,
            activities.lead_qualify,
            activities.verify_kyc,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
