from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_redis_gateway.api import is_authorized_header, parse_wait_timeout


def test_parse_wait_timeout_rejects_non_integer_before_enqueue() -> None:
    settings = SimpleNamespace(sync_wait_timeout_seconds=180)

    with pytest.raises(ValueError):
        parse_wait_timeout({"prompt": "hello", "waitTimeoutSeconds": "later"}, settings)  # type: ignore[arg-type]


def test_parse_wait_timeout_uses_default() -> None:
    settings = SimpleNamespace(sync_wait_timeout_seconds=180)

    assert parse_wait_timeout({"prompt": "hello"}, settings) == 180  # type: ignore[arg-type]


def test_authorization_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_compare_digest(left: str, right: str) -> bool:
        calls.append((left, right))
        return True

    monkeypatch.setattr("hermes_redis_gateway.api.secrets.compare_digest", fake_compare_digest)

    assert is_authorized_header("Bearer secret", "secret")
    assert calls == [("Bearer secret", "Bearer secret")]
