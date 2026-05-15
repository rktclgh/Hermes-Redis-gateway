import importlib

import pytest

from hermes_redis_gateway.config import Settings


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


def test_slot_profile_prefix_resolves_to_runtime_model() -> None:
    settings = Settings(
        hermes_model="gpt-5.4-mini",
        allowed_models=frozenset({"gpt-5.4-mini"}),
        slot_profile_prefix="vlainter-stateless-llm",
    )

    assert settings.requested_model("vlainter-stateless-llm") == "vlainter-stateless-llm"
    assert settings.runtime_model_for("vlainter-stateless-llm") == "gpt-5.4-mini"


def test_runtime_model_rejects_unknown_public_model() -> None:
    settings = Settings(
        hermes_model="gpt-5.4-mini",
        allowed_models=frozenset({"gpt-5.4-mini"}),
        slot_profile_prefix="vlainter-stateless-llm",
    )

    with pytest.raises(ValueError, match="model is not allowed: nope"):
        settings.runtime_model_for("nope")
