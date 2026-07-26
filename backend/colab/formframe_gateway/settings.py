from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class GatewaySettings:
    root: Path
    comfy_url: str
    access_team_domain: str
    access_audience: str
    development_token: str
    runtime_id: str
    max_queue_size: int
    max_asset_bytes: int

    @classmethod
    def from_environment(cls) -> "GatewaySettings":
        return cls(
            root=Path(os.environ.get("FORMFRAME_REMOTE_ROOT", "/content/formframe")).resolve(),
            comfy_url=os.environ.get("FORMFRAME_COMFY_URL", "http://127.0.0.1:8188").rstrip("/"),
            access_team_domain=os.environ.get("FORMFRAME_CF_ACCESS_TEAM_DOMAIN", "").strip(),
            access_audience=os.environ.get("FORMFRAME_CF_ACCESS_AUDIENCE", "").strip(),
            development_token=os.environ.get("FORMFRAME_GATEWAY_DEVELOPMENT_TOKEN", "").strip(),
            runtime_id=os.environ.get("FORMFRAME_RUNTIME_ID", "").strip() or "formframe-colab",
            max_queue_size=int(os.environ.get("FORMFRAME_GATEWAY_MAX_QUEUE_SIZE", "8")),
            max_asset_bytes=int(os.environ.get("FORMFRAME_GATEWAY_MAX_ASSET_BYTES", str(64 * 1024 * 1024))),
        )

    def validate(self) -> None:
        parsed = urlparse(self.comfy_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("ComfyUI must be bound to a private loopback URL")
        if self.max_queue_size < 1:
            raise RuntimeError("FORMFRAME_GATEWAY_MAX_QUEUE_SIZE must be at least 1")
        if self.max_asset_bytes < 1:
            raise RuntimeError("FORMFRAME_GATEWAY_MAX_ASSET_BYTES must be at least 1")
        if not self.development_token and not (
            self.access_team_domain and self.access_audience
        ):
            raise RuntimeError(
                "Cloudflare Access issuer/audience are required outside explicit development mode"
            )

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def work(self) -> Path:
        return self.root / "jobs"

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"
