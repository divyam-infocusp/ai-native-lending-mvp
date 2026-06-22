"""
Notifications adapter (#11) — email / SMS / WhatsApp on the S0 harness (#1).

Mock + real-ready, **idempotent**: a given notification (application + type +
channel) is dispatched exactly once. A workflow replay or retry must never
re-send, so the applicant won't get duplicate "your offer is ready" messages —
the harness key (application_id + provider + purpose) guarantees one dispatch.

The real adapter subclasses Adapter and calls the provider API in `_execute`;
the mock records the dispatch and returns a synthetic message id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .base import Adapter, AdapterRequest
from .registry import AdapterHarness

NOTIFICATIONS_PROVIDER = "notifications"

EMAIL = "email"
SMS = "sms"
WHATSAPP = "whatsapp"


@dataclass(frozen=True)
class NotificationReceipt:
    application_id: str
    notif_type: str
    channel: str
    dispatched: bool            # False when served from cache (already sent)
    message_id: Optional[str]


def _purpose(notif_type: str, channel: str) -> str:
    return f"{notif_type}:{channel}"


class MockNotificationsAdapter(Adapter):
    """Mock dispatcher: records each dispatch and returns a synthetic id. Any
    notification type/channel is accepted (unlike a fixed-fixture mock)."""

    provider = NOTIFICATIONS_PROVIDER

    def __init__(self) -> None:
        self.execution_count = 0
        self.sent: list[dict] = []   # dispatched notifications, for assertions

    def _execute(self, request: AdapterRequest) -> dict:
        self.execution_count += 1
        record = {
            "application_id": request.application_id,
            "purpose": request.purpose,
            "payload": request.payload,
        }
        self.sent.append(record)
        return {
            "message_id": f"msg-{request.application_id}-{request.purpose}",
            "status": "sent",
            "channel": request.purpose.split(":")[-1],
        }


def send_notification(
    harness: AdapterHarness,
    application_id: str,
    *,
    notif_type: str,
    channel: str = EMAIL,
    payload: Optional[dict] = None,
) -> NotificationReceipt:
    """Dispatch a notification idempotently. A repeat for the same (application,
    type, channel) is a no-op send (`dispatched=False`)."""
    resp = harness.call(AdapterRequest(
        application_id, NOTIFICATIONS_PROVIDER, _purpose(notif_type, channel), payload or {},
    ))
    return NotificationReceipt(
        application_id=application_id,
        notif_type=notif_type,
        channel=channel,
        dispatched=not resp.from_cache,
        message_id=resp.data.get("message_id"),
    )


def make_mock_notifications_harness() -> tuple[AdapterHarness, MockNotificationsAdapter]:
    adapter = MockNotificationsAdapter()
    harness = AdapterHarness()
    harness.register(adapter)
    return harness, adapter
