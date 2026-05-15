from __future__ import annotations

import signal
import threading
import time
from typing import Any

from .config import load_settings
from .hermes_runner import HermesInterruptedError, HermesRunner, HermesRunError, HermesTimeoutError
from .redis_store import JobStore, redis_client
from .schemas import JobStatus, TERMINAL_STATUSES
from .slot_lease import SlotLease, SlotLeaseManager


STOP = threading.Event()


def _install_signal_handlers() -> None:
    def stop(_signum: int, _frame: Any) -> None:
        STOP.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


class Worker:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.redis = redis_client(self.settings)
        self.store = JobStore(self.redis, self.settings)
        self.slots = SlotLeaseManager(self.redis, self.settings)
        self.runner = HermesRunner(self.settings)

    def run_forever(self) -> None:
        self.store.ensure_group()
        threads = [
            threading.Thread(target=self._loop, name=f"hrg-worker-{index}", daemon=True)
            for index in range(1, self.settings.worker_threads + 1)
        ]
        for thread in threads:
            thread.start()
        print(f"hermes-redis-gateway worker started threads={len(threads)}", flush=True)
        while not STOP.is_set():
            time.sleep(0.5)
        for thread in threads:
            thread.join(timeout=2)

    def _loop(self) -> None:
        while not STOP.is_set():
            min_idle_ms = self._stream_reclaim_min_idle_ms()
            message = self.store.reclaim_stale(min_idle_ms)
            if message is None:
                message = self.store.read_next(self.settings.worker_poll_timeout_seconds)
            if message is None:
                continue
            message_id, job_id = message
            lease = self.slots.acquire(job_id)
            if lease is None:
                self.store.requeue_pending(message_id, job_id)
                continue
            heartbeat_stop = threading.Event()
            lease_lost = threading.Event()
            heartbeat = threading.Thread(
                target=self._heartbeat,
                args=(lease, heartbeat_stop, lease_lost),
                daemon=True,
            )
            heartbeat.start()
            try:
                self._run_job(message_id, job_id, lease, lease_lost)
            finally:
                heartbeat_stop.set()
                self.slots.release(lease)

    def _run_job(self, message_id: str, job_id: str, lease: SlotLease, lease_lost: threading.Event) -> None:
        job = self.store.get(job_id)
        if not job:
            self.store.ack(message_id)
            return
        if JobStatus(job["status"]) in TERMINAL_STATUSES:
            self.store.ack(message_id)
            return

        payload = self.store.get_payload(job_id)
        if payload is None:
            self.store.mark_failed(job_id, JobStatus.FAILED, "job payload is missing", {"slot": lease.name})
            self.store.ack(message_id)
            return

        self.store.mark_running(job_id, slot=lease.name, profile=lease.profile)
        try:
            result = self.runner.run(lease, payload, stop_event=lease_lost)
            if not self.slots.owns(lease):
                self.store.mark_failed(
                    job_id,
                    JobStatus.INTERRUPTED,
                    "slot lease was lost before result could be stored",
                    {"slot": lease.name, "profile": lease.profile},
                )
                self.store.ack(message_id)
                return
            self.store.mark_succeeded(job_id, result)
        except HermesTimeoutError as exc:
            self.store.mark_failed(job_id, JobStatus.TIMEOUT, str(exc), {"slot": lease.name, "profile": lease.profile})
        except HermesInterruptedError as exc:
            self.store.mark_failed(job_id, JobStatus.INTERRUPTED, str(exc), {"slot": lease.name, "profile": lease.profile})
        except HermesRunError as exc:
            self.store.mark_failed(
                job_id,
                JobStatus.FAILED,
                str(exc),
                {"slot": lease.name, "profile": lease.profile, "exitCode": exc.exit_code},
            )
        except Exception as exc:
            self.store.mark_failed(job_id, JobStatus.FAILED, str(exc), {"slot": lease.name, "profile": lease.profile})
        finally:
            self.store.ack(message_id)

    def _heartbeat(self, lease: SlotLease, heartbeat_stop: threading.Event, lease_lost: threading.Event) -> None:
        interval = max(1, self.settings.slot_lease_seconds // 3)
        while not heartbeat_stop.wait(interval):
            if not self.slots.refresh(lease):
                print(f"lost slot lease slot={lease.name}", flush=True)
                lease_lost.set()
                return

    def _stream_reclaim_min_idle_ms(self) -> int:
        min_idle_seconds = max(
            self.settings.slot_lease_seconds,
            self.settings.hermes_timeout_seconds + self.settings.worker_poll_timeout_seconds + 5,
        )
        return min_idle_seconds * 1000


def main() -> None:
    _install_signal_handlers()
    Worker().run_forever()


if __name__ == "__main__":
    main()
