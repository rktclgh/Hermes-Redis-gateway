from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from typing import Any
from uuid import uuid4

from .config import Settings
from .slot_lease import SlotLease


SEED_FILES = (
    ".env",
    "auth.json",
    "config.yaml",
    "models_dev_cache.json",
    "context_length_cache.yaml",
    "SOUL.md",
)


BRIDGE_SCRIPT = """\
from pathlib import Path
import sys

from hermes_cli.oneshot import run_oneshot


def main() -> int:
    prompt_path, provider, model, toolsets = sys.argv[1:5]
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    return run_oneshot(
        prompt,
        provider=provider or None,
        model=model or None,
        toolsets=toolsets or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
"""


class HermesTimeoutError(RuntimeError):
    pass


class HermesRunError(RuntimeError):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class HermesInterruptedError(RuntimeError):
    pass


class HermesRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self, lease: SlotLease, payload: dict[str, Any], stop_event: Any | None = None) -> dict[str, Any]:
        prompt = str(payload["prompt"]).strip()
        requested_model = self.settings.requested_model(payload.get("model"))
        runtime_model = self.settings.runtime_model_for(payload.get("model"))
        slot_home = Path(self.settings.slot_home_root) / lease.name
        slot_workdir = Path(self.settings.slot_workdir_root) / lease.name
        self._prepare_slot(slot_home, slot_workdir)
        bridge_path = self._write_bridge(slot_workdir)
        prompt_path = self._write_prompt_file(slot_workdir, prompt)

        command = self._build_command(bridge_path, prompt_path, runtime_model)
        env = os.environ.copy()
        env["HERMES_HOME"] = str(slot_home)
        env["HERMES_PROFILE"] = lease.profile
        env.setdefault("PYTHONUNBUFFERED", "1")

        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=slot_workdir,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    self._terminate(process)
                    raise HermesInterruptedError("Hermes run interrupted because slot lease was lost")
                if process.poll() is not None:
                    break
                if time.monotonic() - started > self.settings.hermes_timeout_seconds:
                    self._terminate(process)
                    raise HermesTimeoutError("Hermes one-shot timed out")
                time.sleep(0.2)
            stdout, stderr = process.communicate(timeout=2)
        except Exception:
            if process.poll() is None:
                self._terminate(process)
            raise
        finally:
            try:
                prompt_path.unlink(missing_ok=True)
            except Exception:
                pass

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if process.returncode != 0:
            message = stderr.strip()[-2000:] or "Hermes exited with a non-zero status"
            raise HermesRunError(message, process.returncode or -1)

        return {
            "text": stdout.strip(),
            "model": requested_model,
            "runtimeProvider": self.settings.hermes_provider,
            "runtimeModel": runtime_model,
            "slot": lease.name,
            "profile": lease.profile,
            "elapsedMs": elapsed_ms,
        }

    def _prepare_slot(self, slot_home: Path, slot_workdir: Path) -> None:
        slot_home.mkdir(parents=True, exist_ok=True)
        slot_workdir.mkdir(parents=True, exist_ok=True)
        base_home = Path(self.settings.base_hermes_home)
        for name in SEED_FILES:
            source = base_home / name
            target = slot_home / name
            if source.is_file():
                shutil.copy2(source, target)

    def _write_bridge(self, slot_workdir: Path) -> Path:
        bridge_path = slot_workdir / "_hrg_oneshot_bridge.py"
        bridge_path.write_text(BRIDGE_SCRIPT, encoding="utf-8")
        os.chmod(bridge_path, 0o700)
        return bridge_path

    def _write_prompt_file(self, slot_workdir: Path, prompt: str) -> Path:
        prompt_path = slot_workdir / f"prompt-{uuid4().hex}.txt"
        prompt_path.write_text(self._stateless_prompt(prompt), encoding="utf-8")
        os.chmod(prompt_path, 0o600)
        return prompt_path

    def _build_command(self, bridge_path: Path, prompt_path: Path, model: str) -> list[str]:
        return [
            self.settings.hermes_python,
            str(bridge_path),
            str(prompt_path),
            self.settings.hermes_provider,
            model,
            self.settings.hermes_toolsets,
        ]

    def _stateless_prompt(self, prompt: str) -> str:
        return (
            "You are a stateless inference endpoint. "
            "Do not use tools, do not persist memory, and return only the requested answer.\n\n"
            f"{prompt}"
        )

    def _terminate(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except Exception:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
