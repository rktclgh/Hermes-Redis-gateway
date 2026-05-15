import importlib


def test_non_local_bind_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("HRG_HOST", "0.0.0.0")
    monkeypatch.delenv("HRG_API_KEY", raising=False)

    config = importlib.reload(importlib.import_module("hermes_redis_gateway.config"))

    try:
        config.load_settings()
    except ValueError as exc:
        assert "HRG_API_KEY is required" in str(exc)
    else:
        raise AssertionError("non-local bind without API key should fail")


def test_local_bind_can_run_without_api_key(monkeypatch) -> None:
    monkeypatch.setenv("HRG_HOST", "127.0.0.1")
    monkeypatch.delenv("HRG_API_KEY", raising=False)

    config = importlib.reload(importlib.import_module("hermes_redis_gateway.config"))

    assert config.load_settings().host == "127.0.0.1"
