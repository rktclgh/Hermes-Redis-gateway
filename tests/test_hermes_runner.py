from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import hermes_redis_gateway.hermes_runner as hermes_runner_module
from hermes_redis_gateway.hermes_runner import HermesRunner


def test_runner_command_uses_prompt_file_not_prompt_argv() -> None:
    settings = SimpleNamespace(
        hermes_python="/venv/bin/python",
        hermes_provider="openai-codex",
        hermes_toolsets="",
    )
    runner = HermesRunner(settings)  # type: ignore[arg-type]

    command = runner._build_command(Path("/tmp/bridge.py"), Path("/tmp/prompt.txt"), "gpt-5.4-mini")

    assert command == [
        "/venv/bin/python",
        "/tmp/bridge.py",
        "/tmp/prompt.txt",
        "openai-codex",
        "gpt-5.4-mini",
        "",
    ]
    assert "sensitive prompt" not in " ".join(command)


def test_prompt_file_uses_uuid_name(tmp_path: Path) -> None:
    settings = SimpleNamespace()
    runner = HermesRunner(settings)  # type: ignore[arg-type]

    prompt_path = runner._write_prompt_file(tmp_path, "hello")

    assert prompt_path.name.startswith("prompt-")
    assert prompt_path.suffix == ".txt"
    assert len(prompt_path.stem.removeprefix("prompt-")) == 32
    assert "hello" in prompt_path.read_text(encoding="utf-8")


def test_runner_passes_runtime_model_to_hermes_for_public_alias(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def requested_model(payload_model: object | None) -> str:
        return str(payload_model or "gpt-5.4-mini").strip()

    def runtime_model_for(payload_model: object | None) -> str:
        if payload_model == "vlainter-stateless-llm":
            return "gpt-5.4-mini"
        return requested_model(payload_model)

    settings = SimpleNamespace(
        base_hermes_home=str(tmp_path / "base"),
        hermes_model="gpt-5.4-mini",
        hermes_provider="openai-codex",
        hermes_python="/venv/bin/python",
        hermes_timeout_seconds=5,
        hermes_toolsets="",
        requested_model=requested_model,
        runtime_model_for=runtime_model_for,
        slot_home_root=str(tmp_path / "home"),
        slot_workdir_root=str(tmp_path / "work"),
    )
    runner = HermesRunner(settings)  # type: ignore[arg-type]

    class FakeProcess:
        pid = 1234
        returncode = 0

        def __init__(self, command: list[str], **_kwargs: object) -> None:
            captured["command"] = command

        def poll(self) -> int:
            return 0

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            captured["communicateTimeout"] = timeout
            return ("answer", "")

    monkeypatch.setattr(hermes_runner_module.subprocess, "Popen", FakeProcess)

    result = runner.run(
        SimpleNamespace(name="slot-1", profile="vlainter-stateless-llm-1"),
        {"prompt": "hello", "model": "vlainter-stateless-llm"},
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[-2] == "gpt-5.4-mini"
    assert result["model"] == "vlainter-stateless-llm"
    assert result["runtimeModel"] == "gpt-5.4-mini"
