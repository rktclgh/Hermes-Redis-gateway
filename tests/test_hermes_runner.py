from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
