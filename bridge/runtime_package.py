from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeSecrets:
    tunnel_token: str
    access_team_domain: str
    access_audience: str
    development_token: str = ""
    github_repo_url: str = ""
    github_revision: str = ""
    github_token: str = ""

    def validate(self) -> None:
        if not self.tunnel_token:
            raise ValueError("Cloudflare named-tunnel token is required")
        if not self.development_token and not (self.access_team_domain and self.access_audience):
            raise ValueError("Cloudflare Access team domain and audience are required")
        if not self.github_repo_url:
            raise ValueError("GitHub repository URL is required")
        if not self.github_revision:
            raise ValueError("GitHub revision is required")

    def document(self) -> dict[str, str]:
        self.validate()
        return {
            "tunnel_token": self.tunnel_token,
            "access_team_domain": self.access_team_domain,
            "access_audience": self.access_audience,
            "development_token": self.development_token,
            "github_repo_url": self.github_repo_url,
            "github_revision": self.github_revision,
            "github_token": self.github_token,
        }


def build_runtime_archive(repo_root: Path, destination: Path) -> Path:
    required_roots = (
        repo_root / "backend" / "colab",
        repo_root / "comfy" / "custom_nodes" / "formframe_nodes",
        repo_root / "comfy" / "workflows",
    )
    for root in required_roots:
        if not root.is_dir():
            raise FileNotFoundError(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for root in required_roots:
            for path in sorted(root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    archive.write(path, path.relative_to(repo_root))
    return destination


def write_runtime_secrets(secrets: RuntimeSecrets, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(secrets.document(), sort_keys=True) + "\n", encoding="utf-8")
    destination.chmod(0o600)
    return destination
