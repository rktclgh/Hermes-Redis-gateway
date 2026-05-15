from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
from typing import Any
from urllib.parse import urlparse

from redis.exceptions import RedisError

from .config import load_settings, Settings
from .http_utils import read_json, write_json
from .redis_store import JobStore, QueueFullError, redis_client
from .schemas import JobStatus, TERMINAL_STATUSES, require_prompt
from .slot_lease import SlotLeaseManager


SETTINGS = load_settings()
REDIS = redis_client(SETTINGS)
STORE = JobStore(REDIS, SETTINGS)
SLOTS = SlotLeaseManager(REDIS, SETTINGS)


class Handler(BaseHTTPRequestHandler):
    server_version = "hermes-redis-gateway/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._health()
            return
        if parsed.path.startswith("/jobs/"):
            self._get_job(parsed.path.rsplit("/", 1)[-1])
            return
        write_json(self, 404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/generate", "/jobs"}:
            write_json(self, 404, {"error": "not_found"})
            return
        if not self._authorized():
            write_json(self, 401, {"error": "unauthorized"})
            return

        try:
            payload = read_json(self, max_body_bytes=SETTINGS.max_prompt_bytes + 4096)
            require_prompt(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            write_json(self, 400, {"error": "invalid_request", "message": str(exc)})
            return

        service = self.headers.get("X-HRG-Service", "unknown").strip() or "unknown"
        try:
            job_id = STORE.enqueue(payload, service=service)
        except QueueFullError as exc:
            write_json(self, 429, {"error": "queue_full", "message": str(exc)}, {"Retry-After": "10"})
            return
        except RedisError as exc:
            write_json(self, 503, {"error": "redis_unavailable", "message": str(exc)})
            return
        except ValueError as exc:
            write_json(self, 400, {"error": "invalid_request", "message": str(exc)})
            return

        if parsed.path == "/jobs":
            write_json(self, 202, {"jobId": job_id, "status": JobStatus.QUEUED.value})
            return

        try:
            requested_wait = int(payload.get("waitTimeoutSeconds") or SETTINGS.sync_wait_timeout_seconds)
        except (TypeError, ValueError):
            write_json(self, 400, {"error": "invalid_request", "message": "waitTimeoutSeconds must be an integer"})
            return
        self._wait_for_job(job_id, min(requested_wait, SETTINGS.max_wait_timeout_seconds))

    def _health(self) -> None:
        try:
            REDIS.ping()
            redis_status = "UP"
        except Exception:
            redis_status = "DOWN"
        status = 200 if redis_status == "UP" else 503
        write_json(
            self,
            status,
            {
                "status": "UP" if redis_status == "UP" else "DOWN",
                "redis": redis_status,
                "queueBacklog": _queue_backlog() if redis_status == "UP" else None,
                "slotPool": SLOTS.snapshot() if redis_status == "UP" else None,
            },
        )

    def _get_job(self, job_id: str) -> None:
        if not self._authorized():
            write_json(self, 401, {"error": "unauthorized"})
            return
        job = STORE.get(job_id)
        if not job:
            write_json(self, 404, {"error": "job_not_found"})
            return
        write_json(self, 200, job)

    def _wait_for_job(self, job_id: str, timeout_seconds: int) -> None:
        deadline = time.monotonic() + max(1, timeout_seconds)
        while time.monotonic() < deadline:
            job = STORE.get(job_id)
            if not job:
                write_json(self, 404, {"error": "job_not_found", "jobId": job_id})
                return
            status = JobStatus(job["status"])
            if status in TERMINAL_STATUSES:
                if status == JobStatus.SUCCEEDED:
                    result = dict(job["result"])
                    result.setdefault("jobId", job_id)
                    write_json(self, 200, result)
                    return
                response_status = 504 if status == JobStatus.TIMEOUT else 502
                write_json(self, response_status, {"error": status.value.lower(), "job": job})
                return
            time.sleep(0.25)
        write_json(self, 202, {"jobId": job_id, "status": "WAIT_TIMEOUT"}, {"Retry-After": "2"})

    def _authorized(self) -> bool:
        if not SETTINGS.api_key:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {SETTINGS.api_key}"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def serve(settings: Settings = SETTINGS) -> None:
    server = ThreadingHTTPServer((settings.host, settings.port), Handler)
    print(f"hermes-redis-gateway api listening on http://{settings.host}:{settings.port}", flush=True)
    server.serve_forever()


def _queue_backlog() -> int:
    value = REDIS.get(SETTINGS.queue_count_key)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return int(value or 0)


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
