import json

from hermes_redis_gateway.redis_store import JobStore


class Settings:
    allowed_models = frozenset({"gpt-5.4-mini"})
    hermes_model = "gpt-5.4-mini"
    job_prefix = "job:"
    job_ttl_seconds = 60
    max_prompt_bytes = 1000
    queue_count_key = "queue:count"
    queue_max_size = 10
    stream_group = "workers"
    stream_key = "stream"


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, tuple[object, ...]]] = []
        self.xack_calls: list[tuple[str, str, str]] = []

    def eval(self, script: str, key_count: int, *args: object) -> str:
        self.calls.append((script, key_count, args))
        return "1-0"

    def xack(self, stream_key: str, stream_group: str, message_id: str) -> None:
        self.xack_calls.append((stream_key, stream_group, message_id))


def test_public_job_hides_payload_by_default() -> None:
    store = JobStore(client=None, settings=Settings())  # type: ignore[arg-type]
    job = {
        "jobId": "job-1",
        "status": "QUEUED",
        "payload": json.dumps({"prompt": "secret"}),
        "metadata": json.dumps({"requestId": "req-1"}),
    }

    public = store._public_job(job)

    assert "payload" not in public
    assert public["metadata"] == {"requestId": "req-1"}


def test_enqueue_uses_atomic_backlog_counter_key() -> None:
    redis = FakeRedis()
    store = JobStore(client=redis, settings=Settings())  # type: ignore[arg-type]

    job_id = store.enqueue({"prompt": "hello"}, service="svc")

    assert len(job_id) == 32
    _script, key_count, args = redis.calls[0]
    assert key_count == 3
    assert args[0] == "stream"
    assert args[2] == "queue:count"


def test_ack_decrements_backlog_counter_through_lua() -> None:
    redis = FakeRedis()
    store = JobStore(client=redis, settings=Settings())  # type: ignore[arg-type]

    store.ack("1-0")

    _script, key_count, args = redis.calls[0]
    assert key_count == 2
    assert args == ("stream", "queue:count", "workers", "1-0")


def test_ack_without_counter_does_not_touch_backlog_counter() -> None:
    redis = FakeRedis()
    store = JobStore(client=redis, settings=Settings())  # type: ignore[arg-type]

    store.ack_without_counter("1-0")

    assert redis.calls == []
    assert redis.xack_calls == [("stream", "workers", "1-0")]


def test_parse_stream_response_preserves_message_id_without_job_id() -> None:
    store = JobStore(client=None, settings=Settings())  # type: ignore[arg-type]

    response = [(b"stream", [(b"1-0", {b"service": b"svc"})])]

    assert store._parse_stream_response(response) == ("1-0", "")


def test_raw_skips_none_values() -> None:
    class RedisWithNone:
        def hgetall(self, _key: str) -> dict[object, object]:
            return {b"jobId": b"job-1", None: b"ignored", b"status": None}

    store = JobStore(client=RedisWithNone(), settings=Settings())  # type: ignore[arg-type]

    assert store._raw("job-1") == {"jobId": "job-1"}
