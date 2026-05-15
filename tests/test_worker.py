from __future__ import annotations

from types import SimpleNamespace

from hermes_redis_gateway.worker import Worker


def test_reclaim_idle_waits_past_hermes_timeout() -> None:
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(
        hermes_timeout_seconds=180,
        slot_lease_seconds=60,
        worker_poll_timeout_seconds=5,
    )

    assert worker._stream_reclaim_min_idle_ms() == 190_000
