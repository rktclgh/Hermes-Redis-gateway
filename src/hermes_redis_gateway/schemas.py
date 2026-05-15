from __future__ import annotations

from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INTERRUPTED = "INTERRUPTED"


TERMINAL_STATUSES = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.TIMEOUT, JobStatus.INTERRUPTED}


def require_prompt(payload: dict[str, Any]) -> str:
    prompt = str(payload.get("prompt", "")).strip()
    if not prompt:
        raise ValueError("prompt is required")
    return prompt


def public_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    allowed = {}
    for key in ("requestId", "traceId", "userId", "sessionId"):
        value = metadata.get(key)
        if value is not None:
            allowed[key] = str(value)[:200]
    return allowed
