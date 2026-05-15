from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_redis_gateway.api import parse_wait_timeout


def test_parse_wait_timeout_rejects_non_integer_before_enqueue() -> None:
    settings = SimpleNamespace(sync_wait_timeout_seconds=180)

    with pytest.raises(ValueError):
        parse_wait_timeout({"prompt": "hello", "waitTimeoutSeconds": "later"}, settings)  # type: ignore[arg-type]


def test_parse_wait_timeout_uses_default() -> None:
    settings = SimpleNamespace(sync_wait_timeout_seconds=180)

    assert parse_wait_timeout({"prompt": "hello"}, settings) == 180  # type: ignore[arg-type]
