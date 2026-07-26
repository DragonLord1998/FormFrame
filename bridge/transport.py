from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .colab_cli import ColabCli


@dataclass(frozen=True)
class TransferMetrics:
    upload_mbps: float
    download_mbps: float
    latency_ms: float
    measured: bool = True

    def estimate_seconds(self, byte_count: int, direction: str = "upload") -> float:
        throughput = self.upload_mbps if direction == "upload" else self.download_mbps
        return self.latency_ms / 1000 + (byte_count * 8) / max(throughput * 1_000_000, 1)


class TransferBackend(ABC):
    @abstractmethod
    def upload(self, local_path: Path, remote_path: str) -> None:
        """Upload one immutable bundle or asset."""

    @abstractmethod
    def download(self, remote_path: str, local_path: Path) -> None:
        """Download one preview or final output."""

    @abstractmethod
    def benchmark(self) -> TransferMetrics:
        """Measure the active session rather than assuming a fastest route."""


class LocalPreviewTransfer(TransferBackend):
    """Filesystem transport used by the deterministic local preview provider."""

    def upload(self, local_path: Path, remote_path: str) -> None:
        if not local_path.is_file():
            raise FileNotFoundError(local_path)

    def download(self, remote_path: str, local_path: Path) -> None:
        source = Path(remote_path)
        local_path.write_bytes(source.read_bytes())

    def benchmark(self) -> TransferMetrics:
        return TransferMetrics(upload_mbps=2400, download_mbps=2400, latency_ms=0.3)


class UnconfiguredRemoteTransfer(TransferBackend):
    def __init__(self, name: str) -> None:
        self.name = name

    def _raise(self) -> None:
        raise RuntimeError(f"{self.name} transfer is not configured")

    def upload(self, local_path: Path, remote_path: str) -> None:
        self._raise()

    def download(self, remote_path: str, local_path: Path) -> None:
        self._raise()

    def benchmark(self) -> TransferMetrics:
        self._raise()


class ColabCLITransfer(TransferBackend):
    def __init__(self, cli: ColabCli) -> None:
        self.cli = cli

    def upload(self, local_path: Path, remote_path: str) -> None:
        self.cli.upload(local_path, remote_path)

    def download(self, remote_path: str, local_path: Path) -> None:
        self.cli.download(remote_path, local_path)

    def benchmark(self) -> TransferMetrics:
        # The CLI has high process startup overhead; throughput is measured by the
        # runtime manager with a real probe asset once a Colab session is active.
        return TransferMetrics(upload_mbps=0, download_mbps=0, latency_ms=0, measured=False)
