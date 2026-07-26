from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _path(name: str, default: str = "") -> Path | None:
    value = _value(name, default)
    if not value:
        return None
    path = Path(value).expanduser()
    # Keep executable symlinks intact: resolving a virtualenv's `bin/python`
    # returns the base interpreter and silently bypasses the virtualenv.
    return path if path.is_absolute() else Path.cwd() / path


COMMIT_RE = re.compile(r"^[a-fA-F0-9]{40}$")


def _smplx_model_file(model_dir: Path) -> Path | None:
    for candidate in (model_dir, model_dir / "smplx", model_dir / "SMPLX"):
        for filename in ("SMPLX_NEUTRAL.npz", "SMPLX_NEUTRAL.pkl"):
            path = candidate / filename
            if path.is_file():
                return path
    return None


@dataclass(frozen=True)
class FormFrameSettings:
    colab_cli: Path | None
    colab_auth: str
    colab_session: str
    colab_gpu: str
    colab_config: Path | None
    github_repo_url: str
    github_revision: str
    github_token: str
    gateway_url: str
    cloudflare_client_id: str
    cloudflare_client_secret: str
    cloudflare_tunnel_mode: str
    cloudflare_tunnel_token: str
    cloudflare_access_team_domain: str
    cloudflare_access_audience: str
    gateway_development_token: str
    smplx_model_dir: Path | None
    gnm_checkout: Path | None
    geometry_python: Path | None
    remote_cache_dir: Path | None

    @classmethod
    def from_environment(cls) -> "FormFrameSettings":
        discovered_cli = shutil.which("colab") or ""
        return cls(
            colab_cli=_path("FORMFRAME_COLAB_CLI", discovered_cli),
            colab_auth=_value("FORMFRAME_COLAB_AUTH", "adc"),
            colab_session=_value("FORMFRAME_COLAB_SESSION", "formframe-a100"),
            colab_gpu=_value("FORMFRAME_COLAB_GPU", "A100"),
            colab_config=_path("FORMFRAME_COLAB_CONFIG"),
            github_repo_url=_value("FORMFRAME_GITHUB_REPO_URL"),
            github_revision=_value("FORMFRAME_GITHUB_REVISION"),
            github_token=_value("FORMFRAME_GITHUB_TOKEN"),
            gateway_url=_value("FORMFRAME_GATEWAY_URL").rstrip("/"),
            cloudflare_client_id=_value("FORMFRAME_CF_ACCESS_CLIENT_ID"),
            cloudflare_client_secret=_value("FORMFRAME_CF_ACCESS_CLIENT_SECRET"),
            cloudflare_tunnel_mode=_value("FORMFRAME_CF_TUNNEL_MODE", "managed").lower(),
            cloudflare_tunnel_token=_value("FORMFRAME_CF_TUNNEL_TOKEN"),
            cloudflare_access_team_domain=_value("FORMFRAME_CF_ACCESS_TEAM_DOMAIN"),
            cloudflare_access_audience=_value("FORMFRAME_CF_ACCESS_AUDIENCE"),
            gateway_development_token=_value("FORMFRAME_GATEWAY_DEVELOPMENT_TOKEN"),
            smplx_model_dir=_path("FORMFRAME_SMPLX_MODEL_DIR"),
            gnm_checkout=_path("FORMFRAME_GNM_CHECKOUT"),
            geometry_python=_path("FORMFRAME_GEOMETRY_PYTHON"),
            remote_cache_dir=_path("FORMFRAME_REMOTE_CACHE_DIR"),
        )

    @property
    def colab_cli_available(self) -> bool:
        return bool(self.colab_cli and self.colab_cli.is_file())

    @property
    def gateway_configured(self) -> bool:
        if self.cloudflare_tunnel_mode == "quick":
            return True
        if not self.gateway_url:
            return False
        parsed = urlparse(self.gateway_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        has_access_token = bool(self.cloudflare_client_id and self.cloudflare_client_secret)
        return has_access_token

    @property
    def smplx_assets_available(self) -> bool:
        if not self.smplx_model_dir:
            return False
        return _smplx_model_file(self.smplx_model_dir) is not None

    @property
    def gnm_assets_available(self) -> bool:
        if not self.gnm_checkout or not self.gnm_checkout.is_dir():
            return False
        candidates = (
            self.gnm_checkout / "gnm" / "shape" / "data" / "versions" / "v3_0" / "gnm_head.npz",
            self.gnm_checkout / "shape" / "data" / "versions" / "v3_0" / "gnm_head.npz",
        )
        return any(path.is_file() for path in candidates)

    def remote_readiness_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.colab_cli_available:
            errors.append("FORMFRAME_COLAB_CLI does not point to the Google Colab CLI executable")
        if self.colab_gpu.upper() != "A100":
            errors.append("FORMFRAME_COLAB_GPU must be A100")
        if not self.github_repo_url:
            errors.append("FORMFRAME_GITHUB_REPO_URL is required for Colab source checkout")
        if not COMMIT_RE.fullmatch(self.github_revision):
            errors.append("FORMFRAME_GITHUB_REVISION must be a full 40-character Git commit SHA")
        if self.cloudflare_tunnel_mode not in {"managed", "quick"}:
            errors.append("FORMFRAME_CF_TUNNEL_MODE must be managed or quick")
        elif self.cloudflare_tunnel_mode == "managed":
            if not self.gateway_configured:
                errors.append(
                    "Configure FORMFRAME_GATEWAY_URL and Cloudflare Access service-token credentials"
                )
            if not self.cloudflare_tunnel_token:
                errors.append(
                    "FORMFRAME_CF_TUNNEL_TOKEN is required for the managed Cloudflare tunnel"
                )
            if not (
                self.cloudflare_access_team_domain and self.cloudflare_access_audience
            ):
                errors.append(
                    "Configure FORMFRAME_CF_ACCESS_TEAM_DOMAIN and FORMFRAME_CF_ACCESS_AUDIENCE"
                )
        if not self.smplx_assets_available:
            errors.append(
                "Set FORMFRAME_SMPLX_MODEL_DIR to licensed SMPL-X model files"
            )
        if not self.gnm_assets_available:
            errors.append("Set FORMFRAME_GNM_CHECKOUT to a complete google/gnm checkout")
        return errors
