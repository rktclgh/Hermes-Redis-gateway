from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

from hermes_redis_gateway.slot_lease import SlotLease
from hermes_redis_gateway import worker as worker_module
from hermes_redis_gateway.worker import Worker


class RefreshingSlots:
    def __init__(self, refresh_result: bool) -> None:
        self.refresh_result = refresh_result
        self.refresh_calls = 0

    def refresh(self, _lease: SlotLease) -> bool:
        self.refresh_calls += 1
        return self.refresh_result


class SlotsShouldNotAcquire:
    def acquire(self, _job_id: str) -> None:
        raise AssertionError("malformed stream messages must be acknowledged before slot acquisition")


class StoreWithMalformedMessage:
    def __init__(self) -> None:
        self.stream_acked: list[str] = []

    def reclaim_stale(self, _min_idle_ms: int) -> None:
        return None

    def read_next(self, _timeout_seconds: int) -> tuple[str, str]:
        return "1-0", ""

    def ack_without_counter(self, message_id: str) -> None:
        self.stream_acked.append(message_id)
        worker_module.STOP.set()


def test_reclaim_idle_waits_past_hermes_timeout() -> None:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        hermes_timeout_seconds=180,
        slot_lease_seconds=60,
        worker_poll_timeout_seconds=5,
    )

    assert worker._stream_reclaim_min_idle_ms() == 190_000


def test_loop_logs_and_acknowledges_malformed_stream_message_without_slot_acquire(
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        hermes_timeout_seconds=180,
        slot_lease_seconds=60,
        worker_poll_timeout_seconds=5,
    )
    worker.store = StoreWithMalformedMessage()
    worker.slots = SlotsShouldNotAcquire()

    with caplog.at_level(logging.WARNING, logger="hermes_redis_gateway.worker"):
        worker_module.STOP.clear()
        try:
            worker._loop()
        finally:
            worker_module.STOP.clear()

    assert worker.store.stream_acked == ["1-0"]
    assert "dropping malformed stream message without jobId message_id=1-0" in caplog.text


def test_refresh_lease_reports_lost_slot() -> None:
    worker = Worker.__new__(Worker)
    worker.slots = RefreshingSlots(refresh_result=False)
    lease = SlotLease(index=1, name="slot-1", profile="profile-1", token="token")

    refreshed_at = worker._refresh_lease_if_needed(lease, last_refresh=-999, refresh_interval=1)

    assert refreshed_at is None
    assert worker.slots.refresh_calls == 1


def test_heartbeat_marks_lease_lost_when_stop_is_set() -> None:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(slot_lease_seconds=240)
    worker.slots = RefreshingSlots(refresh_result=True)
    lease_lost = threading.Event()

    worker_module.STOP.set()
    try:
        worker._heartbeat(
            SlotLease(index=1, name="slot-1", profile="profile-1", token="token"),
            threading.Event(),
            lease_lost,
        )
    finally:
        worker_module.STOP.clear()

    assert lease_lost.is_set()
    assert worker.slots.refresh_calls == 0
