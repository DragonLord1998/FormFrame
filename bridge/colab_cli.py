from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

PROBE_MARKER = "FORMFRAME_PROBE_JSON:"
MAX_OUTPUT = 64 * 1024
SAFE_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ColabCliError(RuntimeError):
    pass


@dataclass(frozen=True)
class ColabCliConfig:
    executable: Path
    session_name: str = "formframe-a100"
    gpu: str = "A100"
    auth_provider: str = "adc"
    config_path: Path | None = None


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def _redact(value: str) -> str:
    value = value[-MAX_OUTPUT:]
    value = re.sub(r"(?i)(--(?:tunnel-)?token\s+)\S+", r"\1[REDACTED]", value)
    value = re.sub(
        r"(?i)(authorization|token|client-secret|google_application_credentials)(\s*[:=]\s*)\S+",
        r"\1\2[REDACTED]",
        value,
    )
    return value


class ColabCli:
    """Small, shell-free wrapper around the official Google Colab CLI."""

    def __init__(self, config: ColabCliConfig) -> None:
        self.config = config

    def _base(self) -> list[str]:
        if not self.config.executable.is_file():
            raise ColabCliError(f"Colab CLI is missing: {self.config.executable}")
        if not SAFE_SESSION.fullmatch(self.config.session_name):
            raise ColabCliError("Colab session name contains unsupported characters")
        command = [str(self.config.executable)]
        if self.config.auth_provider:
            command.extend(["--auth", self.config.auth_provider])
        if self.config.config_path:
            command.extend(["--config", str(self.config.config_path)])
        return command

    def run(
        self,
        arguments: list[str],
        *,
        timeout_seconds: float = 60,
        check: bool = True,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        command = [*self._base(), *arguments]
        merged_environment = os.environ.copy()
        if environment:
            merged_environment.update(environment)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
                env=merged_environment,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ColabCliError(f"Colab CLI timed out after {timeout_seconds:g}s") from exc
        result = CommandResult(
            tuple(command),
            completed.returncode,
            _redact(completed.stdout),
            _redact(completed.stderr),
        )
        if check and result.returncode:
            raise ColabCliError(result.stderr or result.stdout or "Colab CLI command failed")
        return result

    def sessions(self) -> CommandResult:
        return self.run(["sessions"], check=False)

    def status(self) -> CommandResult:
        return self.run(["status", "-s", self.config.session_name], check=False)

    def ensure_a100_session(self) -> CommandResult:
        if self.config.gpu.upper() != "A100":
            raise ColabCliError("FormFrame requires an A100 Colab runtime")
        current = self.status()
        if current.returncode == 0:
            return current
        return self.run(
            ["new", "-s", self.config.session_name, "--gpu", "A100"],
            timeout_seconds=240,
        )

    def stop(self) -> CommandResult:
        return self.run(
            ["stop", "-s", self.config.session_name],
            timeout_seconds=120,
            check=False,
        )

    def upload(self, local_path: Path, remote_path: str, *, timeout_seconds: float = 600) -> CommandResult:
        if not local_path.is_file():
            raise ColabCliError(f"Upload source is missing: {local_path}")
        return self.run(
            ["upload", "-s", self.config.session_name, str(local_path), remote_path],
            timeout_seconds=timeout_seconds,
        )

    def download(
        self,
        remote_path: str,
        local_path: Path,
        *,
        timeout_seconds: float = 600,
    ) -> CommandResult:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        return self.run(
            ["download", "-s", self.config.session_name, remote_path, str(local_path)],
            timeout_seconds=timeout_seconds,
        )

    def exec_file(self, local_path: Path, *, timeout_seconds: float = 600) -> CommandResult:
        if not local_path.is_file():
            raise ColabCliError(f"Remote script is missing: {local_path}")
        return self.run(
            [
                "exec",
                "-s",
                self.config.session_name,
                "-f",
                str(local_path),
                "--timeout",
                str(timeout_seconds),
            ],
            timeout_seconds=timeout_seconds + 30,
        )

    def exec_source(self, source: str, label: str, *, timeout_seconds: float = 120) -> CommandResult:
        if not SAFE_SESSION.fullmatch(label):
            raise ColabCliError("Colab script label contains unsupported characters")
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=f"_{label}.py",
            delete=False,
        ) as handle:
            handle.write(source)
            path = Path(handle.name)
        try:
            return self.exec_file(path, timeout_seconds=timeout_seconds)
        finally:
            path.unlink(missing_ok=True)

    def probe(self, attempts: int = 3) -> dict[str, object]:
        for attempt in range(attempts):
            try:
                result = self.exec_source(_probe_source(), "probe", timeout_seconds=90)
                return parse_probe(result.stdout)
            except ColabCliError:
                if attempt + 1 == attempts:
                    raise
                time.sleep(3)
        raise AssertionError("unreachable")


def _probe_source() -> str:
    return """import json
import shutil
import sys

try:
    import torch
    available = bool(torch.cuda.is_available())
    gpu = torch.cuda.get_device_name(0) if available else ""
    vram = int(torch.cuda.get_device_properties(0).total_memory) if available else 0
except Exception:
    available = False
    gpu = ""
    vram = 0

disk = shutil.disk_usage("/content")
print("FORMFRAME_PROBE_JSON:" + json.dumps({
    "python": sys.version.split()[0],
    "cuda_available": available,
    "gpu": gpu,
    "vram_bytes": vram,
    "disk_free_bytes": int(disk.free),
}, sort_keys=True))
"""


def parse_probe(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        if line.startswith(PROBE_MARKER):
            try:
                payload = json.loads(line[len(PROBE_MARKER) :])
            except json.JSONDecodeError as exc:
                raise ColabCliError("Colab GPU probe returned malformed JSON") from exc
            if not isinstance(payload, dict):
                raise ColabCliError("Colab GPU probe returned a non-object")
            return payload
    raise ColabCliError("Colab GPU probe marker was not found")


def require_a100(payload: dict[str, object]) -> None:
    gpu = str(payload.get("gpu", "")).upper()
    vram = payload.get("vram_bytes", 0)
    if (
        payload.get("cuda_available") is not True
        or "A100" not in gpu
        or not isinstance(vram, int)
        or vram < 35 * 1024**3
    ):
        raise ColabCliError(f"Expected A100 runtime, received {gpu or 'no CUDA GPU'}")
