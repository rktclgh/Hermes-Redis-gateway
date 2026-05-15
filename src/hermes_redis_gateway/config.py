from __future__ import annotations

from dataclasses import dataclass
import os
from typing import FrozenSet


def _int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    value = int(raw) if raw else default
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    redis_url: str = os.getenv("HRG_REDIS_URL", "redis://127.0.0.1:6379/0")
    host: str = os.getenv("HRG_HOST", "127.0.0.1")
    port: int = _int_env("HRG_PORT", 8788, 1)
    api_key: str = os.getenv("HRG_API_KEY", "").strip()

    stream_key: str = os.getenv("HRG_STREAM_KEY", "hermes:stream:default")
    stream_group: str = os.getenv("HRG_STREAM_GROUP", "workers")
    stream_consumer: str = os.getenv("HRG_STREAM_CONSUMER", f"worker-{os.getpid()}")
    queue_count_key: str = os.getenv("HRG_QUEUE_COUNT_KEY", "hermes:queue:default:count")
    job_prefix: str = os.getenv("HRG_JOB_PREFIX", "hermes:job:")
    slot_prefix: str = os.getenv("HRG_SLOT_PREFIX", "hermes:slot:")
    queue_max_size: int = _int_env("HRG_QUEUE_MAX_SIZE", 100, 1)
    sync_wait_timeout_seconds: int = _int_env("HRG_SYNC_WAIT_TIMEOUT_SECONDS", 180, 1)
    job_ttl_seconds: int = _int_env("HRG_JOB_TTL_SECONDS", 86400, 60)

    slot_count: int = _int_env("HRG_SLOT_COUNT", 10, 1)
    slot_lease_seconds: int = _int_env("HRG_SLOT_LEASE_SECONDS", 240, 10)
    slot_acquire_timeout_seconds: int = _int_env("HRG_SLOT_ACQUIRE_TIMEOUT_SECONDS", 30, 1)
    worker_threads: int = _int_env("HRG_WORKER_THREADS", 10, 1)
    worker_poll_timeout_seconds: int = _int_env("HRG_WORKER_POLL_TIMEOUT_SECONDS", 5, 1)

    hermes_python: str = os.getenv("HRG_HERMES_PYTHON", "/home/song/.hermes/hermes-agent/venv/bin/python")
    hermes_module: str = os.getenv("HRG_HERMES_MODULE", "hermes_cli.main")
    hermes_provider: str = os.getenv("HRG_HERMES_PROVIDER", "openai-codex")
    hermes_model: str = os.getenv("HRG_HERMES_MODEL", "gpt-5.4-mini")
    allowed_models: FrozenSet[str] = frozenset(
        item.strip()
        for item in os.getenv("HRG_ALLOWED_MODELS", "gpt-5.4-mini,gpt-5.4").split(",")
        if item.strip()
    )
    hermes_timeout_seconds: int = _int_env("HRG_HERMES_TIMEOUT_SECONDS", 180, 1)
    max_prompt_bytes: int = _int_env("HRG_MAX_PROMPT_BYTES", 200_000, 1)
    max_wait_timeout_seconds: int = _int_env("HRG_MAX_WAIT_TIMEOUT_SECONDS", 180, 1)
    base_hermes_home: str = os.getenv("HRG_BASE_HERMES_HOME", "/home/song/.hermes")
    slot_home_root: str = os.getenv("HRG_SLOT_HOME_ROOT", "/home/song/.hermes/hermes-redis-gateway-slots")
    slot_workdir_root: str = os.getenv("HRG_SLOT_WORKDIR_ROOT", "/tmp/hermes-redis-gateway-slots")
    slot_profile_prefix: str = os.getenv("HRG_SLOT_PROFILE_PREFIX", "vlainter-stateless-llm")


def load_settings() -> Settings:
    settings = Settings()
    if settings.host not in {"127.0.0.1", "localhost", "::1"} and not settings.api_key:
        raise ValueError("HRG_API_KEY is required when HRG_HOST is not localhost")
    if settings.hermes_model not in settings.allowed_models:
        raise ValueError("HRG_HERMES_MODEL must be included in HRG_ALLOWED_MODELS")
    return settings
