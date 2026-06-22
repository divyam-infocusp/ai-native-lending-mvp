"""
Tests for the e-Sign + Notifications adapters (#11).

Notifications: dispatched once per key (idempotent), distinct channels/types are
distinct dispatches. e-Sign: idempotent envelope + a completion callback that
yields the signed signal driving OFFER_GENERATED → OFFER_ACCEPTED.
"""
from lending.adapters import (
    EMAIL,
    SMS,
    ESignEnvelope,
    make_mock_esign_harness,
    make_mock_notifications_harness,
    parse_signature_callback,
    request_signature,
    send_notification,
)
from lending.adapters.esign import SENT, SIGNED


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def test_notification_dispatched_once_per_key():
    harness, adapter = make_mock_notifications_harness()
    first = send_notification(harness, "app-1", notif_type="offer_ready", channel=EMAIL)
    second = send_notification(harness, "app-1", notif_type="offer_ready", channel=EMAIL)
    assert adapter.execution_count == 1            # dispatched exactly once
    assert first.dispatched is True
    assert second.dispatched is False              # served from cache, not re-sent
    assert first.message_id == second.message_id


def test_distinct_channel_or_type_is_a_distinct_dispatch():
    harness, adapter = make_mock_notifications_harness()
    send_notification(harness, "app-1", notif_type="offer_ready", channel=EMAIL)
    send_notification(harness, "app-1", notif_type="offer_ready", channel=SMS)     # other channel
    send_notification(harness, "app-1", notif_type="decision", channel=EMAIL)      # other type
    assert adapter.execution_count == 3


def test_notification_records_dispatch_for_audit():
    harness, adapter = make_mock_notifications_harness()
    send_notification(harness, "app-1", notif_type="offer_ready", channel=EMAIL,
                      payload={"amount": 300000})
    assert adapter.sent[0]["application_id"] == "app-1"
    assert adapter.sent[0]["payload"] == {"amount": 300000}


# ---------------------------------------------------------------------------
# e-Sign
# ---------------------------------------------------------------------------

def test_request_signature_is_idempotent():
    harness, adapter = make_mock_esign_harness()
    first = request_signature(harness, "app-1", document_ref="offer.pdf")
    second = request_signature(harness, "app-1", document_ref="offer.pdf")
    assert adapter.execution_count == 1            # one envelope, no double-send
    assert isinstance(first, ESignEnvelope)
    assert first.status == SENT
    assert first.envelope_id == second.envelope_id


def test_signature_callback_signals_completion():
    event = parse_signature_callback({
        "application_id": "app-1", "envelope_id": "env-app-1", "status": SIGNED,
    })
    assert event.signed is True                    # drives OFFER_GENERATED → OFFER_ACCEPTED
    assert event.application_id == "app-1"


def test_unsigned_callback_does_not_signal_completion():
    event = parse_signature_callback({
        "application_id": "app-1", "envelope_id": "env-app-1", "status": "declined",
    })
    assert event.signed is False
