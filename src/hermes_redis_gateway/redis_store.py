from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

import redis
from redis.exceptions import ResponseError

from .config import Settings
from .schemas import JobStatus, public_metadata, require_prompt


class QueueFullError(RuntimeError):
    pass


class JobStore:
    def __init__(self, client: redis.Redis, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    def ensure_group(self) -> None:
        try:
            self.client.xgroup_create(self.settings.stream_key, self.settings.stream_group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def job_key(self, job_id: str) -> str:
        return f"{self.settings.job_prefix}{job_id}"

    def enqueue(self, payload: dict[str, Any], service: str = "unknown") -> str:
        prompt = require_prompt(payload)
        if len(prompt.encode("utf-8")) > self.settings.max_prompt_bytes:
            raise ValueError(f"prompt is too large; max {self.settings.max_prompt_bytes} bytes")
        model = str(payload.get("model") or self.settings.hermes_model).strip()
        if model not in self.settings.allowed_models:
            raise ValueError(f"model is not allowed: {model}")

        job_id = uuid4().hex
        now = str(_epoch_ms())
        job = {
            "jobId": job_id,
            "status": JobStatus.QUEUED.value,
            "service": service[:100],
            "payload": json.dumps(payload, ensure_ascii=False),
            "metadata": json.dumps(public_metadata(payload), ensure_ascii=False),
            "promptLength": str(len(prompt)),
            "createdAt": now,
            "updatedAt": now,
            "attempts": "0",
        }
        script = """
        local backlog = tonumber(redis.call("GET", KEYS[3]) or "0")
        if backlog >= tonumber(ARGV[1]) then
            return {err = "QUEUE_FULL"}
        end
        redis.call("HSET", KEYS[2], unpack(cjson.decode(ARGV[2])))
        redis.call("EXPIRE", KEYS[2], ARGV[3])
        local message_id = redis.call("XADD", KEYS[1], "*", "jobId", ARGV[4], "service", ARGV[5], "createdAt", ARGV[6])
        redis.call("INCR", KEYS[3])
        redis.call("EXPIRE", KEYS[3], ARGV[3])
        return message_id
        """
        flat_job: list[str] = []
        for key, value in job.items():
            flat_job.extend([key, value])
        try:
            self.client.eval(
                script,
                3,
                self.settings.stream_key,
                self.job_key(job_id),
                self.settings.queue_count_key,
                self.settings.queue_max_size,
                json.dumps(flat_job, ensure_ascii=False),
                self.settings.job_ttl_seconds,
                job_id,
                service[:100],
                now,
            )
        except ResponseError as exc:
            if "QUEUE_FULL" in str(exc):
                raise QueueFullError("Hermes job queue is full") from exc
            raise
        return job_id

    def read_next(self, timeout_seconds: int) -> tuple[str, str] | None:
        self.ensure_group()
        response = self.client.xreadgroup(
            groupname=self.settings.stream_group,
            consumername=self.settings.stream_consumer,
            streams={self.settings.stream_key: ">"},
            count=1,
            block=timeout_seconds * 1000,
        )
        return self._parse_stream_response(response)

    def reclaim_stale(self, min_idle_ms: int) -> tuple[str, str] | None:
        self.ensure_group()
        result = self.client.xautoclaim(
            name=self.settings.stream_key,
            groupname=self.settings.stream_group,
            consumername=self.settings.stream_consumer,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=1,
        )
        messages = result[1] if len(result) > 1 else []
        if not messages:
            return None
        message_id, fields = messages[0]
        return _parse_stream_message(message_id, fields)

    def ack(self, message_id: str) -> None:
        script = """
        local acked = redis.call("XACK", KEYS[1], ARGV[1], ARGV[2])
        if acked > 0 then
            local backlog = tonumber(redis.call("GET", KEYS[2]) or "0")
            if backlog > 0 then
                redis.call("DECR", KEYS[2])
            end
        end
        return acked
        """
        self.client.eval(
            script,
            2,
            self.settings.stream_key,
            self.settings.queue_count_key,
            self.settings.stream_group,
            message_id,
        )

    def requeue_pending(self, message_id: str, job_id: str) -> None:
        script = """
        local acked = redis.call("XACK", KEYS[1], ARGV[1], ARGV[2])
        if acked > 0 then
            redis.call("XADD", KEYS[1], "*", "jobId", ARGV[3], "service", ARGV[4], "createdAt", ARGV[5])
        end
        return acked
        """
        raw = self._raw(job_id) or {}
        self.client.eval(
            script,
            1,
            self.settings.stream_key,
            self.settings.stream_group,
            message_id,
            job_id,
            raw.get("service", "unknown"),
            str(_epoch_ms()),
        )

    def get_payload(self, job_id: str) -> dict[str, Any] | None:
        raw = self._raw(job_id)
        if not raw:
            return None
        return json.loads(raw.get("payload", "{}"))

    def get(self, job_id: str, include_payload: bool = False) -> dict[str, Any] | None:
        raw = self._raw(job_id)
        if not raw:
            return None
        return self._public_job(raw, include_payload=include_payload)

    def mark_running(self, job_id: str, slot: str, profile: str) -> None:
        self._update(
            job_id,
            status=JobStatus.RUNNING.value,
            slot=slot,
            profile=profile,
            startedAt=str(_epoch_ms()),
        )
        self.client.hincrby(self.job_key(job_id), "attempts", 1)

    def mark_succeeded(self, job_id: str, result: dict[str, Any]) -> None:
        self._update(
            job_id,
            status=JobStatus.SUCCEEDED.value,
            result=json.dumps(result, ensure_ascii=False),
            completedAt=str(_epoch_ms()),
        )

    def mark_failed(self, job_id: str, status: JobStatus, message: str, details: dict[str, Any] | None = None) -> None:
        self._update(
            job_id,
            status=status.value,
            error=message[:2000],
            errorDetails=json.dumps(details or {}, ensure_ascii=False),
            completedAt=str(_epoch_ms()),
        )

    def _raw(self, job_id: str) -> dict[str, str] | None:
        raw = self.client.hgetall(self.job_key(job_id))
        if not raw:
            return None
        decoded: dict[str, str] = {}
        for key, value in raw.items():
            decoded_key = _decode(key)
            decoded_value = _decode(value)
            if decoded_key is not None and decoded_value is not None:
                decoded[decoded_key] = decoded_value
        return decoded

    def _update(self, job_id: str, **fields: str) -> None:
        fields["updatedAt"] = str(_epoch_ms())
        key = self.job_key(job_id)
        pipe = self.client.pipeline()
        pipe.hset(key, mapping=fields)
        pipe.expire(key, self.settings.job_ttl_seconds)
        pipe.execute()

    def _public_job(self, job: dict[str, str], include_payload: bool = False) -> dict[str, Any]:
        public: dict[str, Any] = {key: value for key, value in job.items() if key != "payload"}
        for json_field in ("result", "errorDetails", "metadata"):
            if json_field in public:
                public[json_field] = json.loads(public[json_field])
        if include_payload and "payload" in job:
            public["payload"] = json.loads(job["payload"])
        return public

    def _parse_stream_response(self, response: Any) -> tuple[str, str] | None:
        if not response:
            return None
        _stream, messages = response[0]
        if not messages:
            return None
        message_id, fields = messages[0]
        return _parse_stream_message(message_id, fields)


def redis_client(settings: Settings) -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url)


def _parse_stream_message(message_id: Any, fields: dict[Any, Any]) -> tuple[str, str] | None:
    decoded_message_id = _decode(message_id)
    if not decoded_message_id:
        return None
    decoded_job_id = _decode(fields.get(b"jobId") or fields.get("jobId")) or ""
    return decoded_message_id, decoded_job_id


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _epoch_ms() -> int:
    return int(time.time() * 1000)
