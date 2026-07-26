from __future__ import annotations

import hashlib
import json
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.cloudflare import CloudflareGateway, GatewayConfig, GatewayError
from bridge.colab_cli import ColabCli, ColabCliConfig, ColabCliError, require_a100
from bridge.runtime_package import RuntimeSecrets, write_runtime_secrets

from .config import FormFrameSettings

ProgressCallback = Callable[[str, str, int, str], None]


class RemoteRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteRenderResult:
    result_path: Path
    preview_path: Path
    result_manifest: dict[str, Any]
    used_cli_fallback: bool
    transfer_plan: dict[str, Any]


@dataclass(frozen=True)
class ReusableAsset:
    path: str
    sha256: str
    bytes: int


class ColabRemoteRuntime:
    def __init__(self, settings: FormFrameSettings, repo_root: Path) -> None:
        errors = settings.remote_readiness_errors()
        if errors:
            raise RemoteRuntimeError("; ".join(errors))
        assert settings.colab_cli is not None
        self.settings = settings
        self.repo_root = repo_root
        self.cli = ColabCli(
            ColabCliConfig(
                executable=settings.colab_cli,
                session_name=settings.colab_session,
                gpu=settings.colab_gpu,
                auth_provider=settings.colab_auth,
                config_path=settings.colab_config,
            )
        )
        self.gateway = CloudflareGateway(
            GatewayConfig(
                base_url=settings.gateway_url,
                access_client_id=settings.cloudflare_client_id,
                access_client_secret=settings.cloudflare_client_secret,
                development_token=settings.gateway_development_token,
            )
        )
        self.probe: dict[str, object] = {}
        self.transfer_metrics: dict[str, Any] = {}
        self.remote_cache_dir = settings.remote_cache_dir or repo_root / "data" / "remote-cache"

    def start(self, progress: ProgressCallback) -> dict[str, Any]:
        progress("provisioning", "Provisioning A100", 8, "Reconnecting or starting formframe-a100")
        self.cli.ensure_a100_session()
        progress("provisioning", "Verifying A100", 16, "Inspecting the actual Colab GPU")
        self.probe = self.cli.probe()
        require_a100(self.probe)
        with tempfile.TemporaryDirectory(prefix="formframe-runtime-") as directory:
            temporary = Path(directory)
            self._write_source_cache_state()
            secrets = write_runtime_secrets(
                RuntimeSecrets(
                    tunnel_token=self.settings.cloudflare_tunnel_token,
                    access_team_domain=self.settings.cloudflare_access_team_domain,
                    access_audience=self.settings.cloudflare_access_audience,
                    development_token=self.settings.gateway_development_token,
                    github_repo_url=self.settings.github_repo_url,
                    github_revision=self.settings.github_revision,
                    github_token=self.settings.github_token,
                ),
                temporary / "runtime.json",
            )
            progress("installing", "Uploading bootstrap inputs", 27, "Sending source checkout configuration")
            self.cli.exec_source(
                """
from pathlib import Path
for value in (
    "/content/formframe/bootstrap",
    "/content/formframe/secrets",
    "/content/formframe/assets",
):
    Path(value).mkdir(parents=True, exist_ok=True)
""",
                "prepare_directories",
            )
            self.cli.upload(secrets, "/content/formframe/secrets/runtime.json")
            progress("restoring", "Restoring model cache", 44, "Installing pinned ComfyUI and model assets")
            bootstrap = self.repo_root / "backend" / "colab" / "bootstrap.py"
            self.cli.exec_file(bootstrap, timeout_seconds=14400)
        progress("warming", "Checking private gateway", 92, "Waiting for the managed Cloudflare route")
        health = self._wait_gateway()
        progress("warming", "Benchmarking transfers", 96, "Measuring Colab CLI and Cloudflare paths")
        self.transfer_metrics = self._benchmark_transfers()
        health["transfer_metrics"] = self.transfer_metrics
        if health.get("gpu") != "A100" or health.get("workflow") != "controlled-character-v1":
            raise RemoteRuntimeError("Remote gateway reported an incompatible runtime")
        return health

    def _write_source_cache_state(self) -> None:
        cache_root = self.remote_cache_dir / "bootstrap"
        cache_root.mkdir(parents=True, exist_ok=True)
        manifest_path = cache_root / "runtime-cache.json"
        model_manifest = self.repo_root / "backend" / "colab" / "model-manifest.json"
        document = {
            "github_repo_url": self.settings.github_repo_url,
            "github_revision": self.settings.github_revision,
            "model_manifest_sha256": _sha256(model_manifest),
            "remote_rehydrate_root": "/content/formframe",
            "remote_source_checkout": "/content/formframe/source",
            "remote_model_cache": "/content/formframe/cache",
            "note": "Local cache stores source and model-manifest metadata only; Colab clones the pinned GitHub revision and model weights rehydrate in the active Colab session.",
        }
        manifest_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _benchmark_transfers(self) -> dict[str, Any]:
        payload = bytes((index * 31) % 256 for index in range(512 * 1024))
        with tempfile.TemporaryDirectory(prefix="formframe-benchmark-") as directory:
            root = Path(directory)
            source = root / "probe.bin"
            destination = root / "probe.returned.bin"
            source.write_bytes(payload)
            remote = "/content/formframe/state/transfer-probe.bin"
            started = time.monotonic()
            self.cli.upload(source, remote, timeout_seconds=180)
            upload_seconds = max(time.monotonic() - started, 1e-6)
            started = time.monotonic()
            self.cli.download(remote, destination, timeout_seconds=180)
            download_seconds = max(time.monotonic() - started, 1e-6)
            if destination.read_bytes() != payload:
                raise RemoteRuntimeError("Colab CLI transfer benchmark hash mismatch")
        cloudflare = self.gateway.benchmark(payload)
        return {
            "colab_cli": {
                "upload_mbps": len(payload) * 8 / upload_seconds / 1_000_000,
                "download_mbps": len(payload) * 8 / download_seconds / 1_000_000,
            },
            "cloudflare": cloudflare,
            "bulk_route": "colab-cli",
            "control_route": "cloudflare",
        }

    def _wait_gateway(self, attempts: int = 90) -> dict[str, Any]:
        last_error = ""
        for attempt in range(attempts):
            try:
                return self.gateway.health()
            except GatewayError as exc:
                last_error = str(exc)
                time.sleep(min(5, 1 + attempt // 10))
        diagnostics = self._cloudflared_log_tail()
        raise RemoteRuntimeError(
            "Cloudflare gateway did not become reachable: "
            f"{last_error}. Remote cloudflared log tail: {diagnostics}"
        )

    def _cloudflared_log_tail(self) -> str:
        source = """
from pathlib import Path
path = Path("/content/formframe/logs/cloudflared.log")
lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
print("\\n".join(lines[-20:]) or "cloudflared log is unavailable")
"""
        try:
            result = self.cli.exec_source(source, "cloudflared_log_tail", timeout_seconds=60)
        except ColabCliError as exc:
            return f"unable to read log ({exc})"
        return result.stdout.strip() or "cloudflared log is empty"

    def render(
        self,
        job_id: str,
        bundle_path: Path,
        local_job_dir: Path,
        progress: Callable[[int, str], None],
    ) -> RemoteRenderResult:
        local_job_dir.mkdir(parents=True, exist_ok=True)
        remote_bundle = f"/content/formframe/inbox/{job_id}.ffjob"
        progress(62, "Negotiating reusable assets")
        reusable_assets = _reusable_assets(bundle_path)
        transfer_plan = self._stage_reusable_assets(bundle_path, reusable_assets)
        upload_bundle = _bundle_without_reusable_assets(
            bundle_path,
            reusable_assets,
            local_job_dir,
        )
        transfer_plan["job_bundle"] = {
            "source_bytes": bundle_path.stat().st_size,
            "uploaded_bytes": upload_bundle.stat().st_size,
            "omitted_reusable_assets": len(reusable_assets),
        }
        identity_lora = _identity_lora_asset(bundle_path)
        if identity_lora:
            progress(64, "Staging trained identity LoRA")
            transfer_plan["identity_lora"] = self._stage_identity_lora(
                identity_lora,
                local_job_dir,
            )
        progress(66, "Uploading immutable .ffjob with Colab CLI")
        self.cli.upload(upload_bundle, remote_bundle)
        used_fallback = False
        try:
            progress(70, "Submitting metadata through Cloudflare")
            self.gateway.submit(job_id, remote_bundle)
            def live_progress(payload: dict[str, Any]) -> None:
                remote_percent = int(payload.get("progress", 40))
                local_percent = min(92, max(72, 70 + remote_percent // 5))
                progress(local_percent, str(payload.get("stage", "Z-Image Turbo rendering")))

            remote = self.gateway.wait_live(
                job_id,
                timeout_seconds=1200,
                on_event=live_progress,
            )
            progress(93, "Downloading preview through Cloudflare")
            self.gateway.download_preview(job_id, local_job_dir / "preview.webp")
            result_path = str(remote.get("result_path") or f"/content/formframe/outbox/{job_id}/result.png")
        except GatewayError:
            used_fallback = True
            progress(72, "Cloudflare unavailable; using Colab CLI fallback")
            source = _cli_fallback_source(job_id, remote_bundle)
            self.cli.exec_source(source, f"render_{job_id}", timeout_seconds=1500)
            result_path = f"/content/formframe/outbox/{job_id}/result.png"
            self.cli.download(
                f"/content/formframe/outbox/{job_id}/preview.webp",
                local_job_dir / "preview.webp",
            )
        progress(96, "Downloading final PNG with Colab CLI")
        local_result = local_job_dir / "result.png"
        local_manifest = local_job_dir / "result.json"
        self.cli.download(result_path, local_result)
        self.cli.download(f"/content/formframe/outbox/{job_id}/result.json", local_manifest)
        document = json.loads(local_manifest.read_text(encoding="utf-8"))
        if document.get("job_id") != job_id:
            raise RemoteRuntimeError("Downloaded result manifest has the wrong job ID")
        actual = hashlib.sha256(local_result.read_bytes()).hexdigest()
        if document.get("output_sha256") != actual:
            raise RemoteRuntimeError("Downloaded final PNG failed its SHA-256 check")
        return RemoteRenderResult(
            result_path=local_result,
            preview_path=local_job_dir / "preview.webp",
            result_manifest=document,
            used_cli_fallback=used_fallback,
            transfer_plan=transfer_plan,
        )

    def cancel(self, job_id: str) -> None:
        try:
            self.gateway.cancel(job_id)
        except GatewayError:
            return

    def stop(self) -> None:
        result = self.cli.stop()
        if result.returncode:
            raise ColabCliError(
                result.stderr or result.stdout or "Colab CLI failed to stop the FormFrame session"
            )

    def _stage_reusable_assets(self, bundle_path: Path, assets: list[ReusableAsset]) -> dict[str, Any]:
        if not assets:
            return {
                "asset_cache": "none",
                "asset_count": 0,
                "missing_count": 0,
                "bulk_route": "colab-cli",
                "control_route": "cloudflare",
            }
        hashes = [asset.sha256 for asset in assets]
        try:
            missing = set(self.gateway.check_assets(hashes))
            cache_checked = True
        except GatewayError:
            missing = set(hashes)
            cache_checked = False
        uploaded = 0
        with zipfile.ZipFile(bundle_path) as archive, tempfile.TemporaryDirectory(
            prefix="formframe-assets-"
        ) as directory:
            temporary = Path(directory)
            by_hash = {asset.sha256: asset for asset in assets}
            for digest in sorted(missing):
                asset = by_hash.get(digest)
                if asset is None:
                    continue
                data = archive.read(asset.path)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise RemoteRuntimeError(f"Reusable asset hash mismatch: {asset.path}")
                local_asset = temporary / digest
                local_asset.write_bytes(data)
                self.cli.upload(local_asset, f"/content/formframe/assets/{digest}")
                uploaded += 1
        return {
            "asset_cache": "content-addressed",
            "asset_count": len(assets),
            "missing_count": len(missing),
            "uploaded_count": uploaded,
            "cache_checked_with_gateway": cache_checked,
            "bulk_route": "colab-cli",
            "control_route": "cloudflare",
        }

    def _stage_identity_lora(
        self,
        asset: ReusableAsset,
        local_job_dir: Path,
    ) -> dict[str, Any]:
        local_root = local_job_dir.resolve().parents[1]
        local_path = local_root / "assets" / asset.sha256
        if (
            not local_path.is_file()
            or local_path.stat().st_size != asset.bytes
            or _sha256(local_path) != asset.sha256
        ):
            raise RemoteRuntimeError("Attached identity LoRA is missing or corrupt")
        remote_root = "/content/formframe/ComfyUI/models/loras"
        remote_path = f"{remote_root}/{asset.path}"
        sidecar_path = f"{remote_path}.sha256"
        probe = f"""
import hashlib
from pathlib import Path
path = Path({remote_path!r})
sidecar = Path({sidecar_path!r})
observed = ""
if path.is_file() and path.stat().st_size == {asset.bytes}:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    observed = digest.hexdigest()
hit = (
    observed == {asset.sha256!r}
)
if hit:
    sidecar.write_text(observed + "\\n", encoding="utf-8")
print("FORMFRAME_LORA_CACHE:" + ("hit" if hit else "miss"))
"""
        checked = self.cli.exec_source(
            probe,
            f"identity_lora_probe_{asset.sha256[:12]}",
            timeout_seconds=60,
        )
        cache_hit = "FORMFRAME_LORA_CACHE:hit" in checked.stdout
        if not cache_hit:
            self.cli.exec_source(
                f"from pathlib import Path\nPath({remote_root!r}).mkdir(parents=True, exist_ok=True)\n",
                f"identity_lora_prepare_{asset.sha256[:12]}",
                timeout_seconds=60,
            )
            self.cli.upload(local_path, remote_path, timeout_seconds=1800)
            verify = f"""
import hashlib
from pathlib import Path
path = Path({remote_path!r})
digest = hashlib.sha256()
with path.open("rb") as stream:
    for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
        digest.update(chunk)
observed = digest.hexdigest()
if path.stat().st_size != {asset.bytes} or observed != {asset.sha256!r}:
    raise RuntimeError("Uploaded identity LoRA failed integrity verification")
Path({sidecar_path!r}).write_text(observed + "\\n", encoding="utf-8")
print("FORMFRAME_LORA_CACHE:verified")
"""
            verified = self.cli.exec_source(
                verify,
                f"identity_lora_verify_{asset.sha256[:12]}",
                timeout_seconds=900,
            )
            if "FORMFRAME_LORA_CACHE:verified" not in verified.stdout:
                raise RemoteRuntimeError("Identity LoRA verification marker is missing")
        return {
            "attached": True,
            "sha256": asset.sha256,
            "bytes": asset.bytes,
            "cache_hit": cache_hit,
            "uploaded": not cache_hit,
            "bulk_route": "colab-cli",
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_entries(value: Any):
    if isinstance(value, dict):
        path = value.get("path")
        digest = value.get("sha256")
        byte_count = value.get("bytes", 0)
        if isinstance(path, str) and isinstance(digest, str):
            yield path, digest, byte_count
        for child in value.values():
            yield from _asset_entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from _asset_entries(child)


def _reusable_assets(bundle_path: Path) -> list[ReusableAsset]:
    core_paths = {"rgb.webp", "depth.png", "pose.png", "normal.png"}
    reusable: list[ReusableAsset] = []
    with zipfile.ZipFile(bundle_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())
        for path, digest, byte_count in _asset_entries(manifest.get("assets")):
            if path in core_paths or path not in names:
                continue
            if Path(path).name != path or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                continue
            data = archive.read(path)
            actual = hashlib.sha256(data).hexdigest()
            if actual != digest:
                raise RemoteRuntimeError(f"Reusable asset hash mismatch: {path}")
            reusable.append(ReusableAsset(path=path, sha256=digest, bytes=int(byte_count or len(data))))
    by_digest = {asset.sha256: asset for asset in reusable}
    return sorted(by_digest.values(), key=lambda asset: asset.sha256)


def _identity_lora_asset(bundle_path: Path) -> ReusableAsset | None:
    with zipfile.ZipFile(bundle_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    value = manifest.get("assets", {}).get("identity_lora")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RemoteRuntimeError("Identity LoRA manifest entry is invalid")
    path = value.get("path")
    digest = value.get("sha256")
    byte_count = value.get("bytes")
    if (
        not isinstance(path, str)
        or not isinstance(digest, str)
        or path != f"formframe_{digest}.safetensors"
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
        or not isinstance(byte_count, int)
        or not 0 < byte_count <= 2 * 1024 * 1024 * 1024
    ):
        raise RemoteRuntimeError("Identity LoRA manifest metadata is invalid")
    return ReusableAsset(path=path, sha256=digest, bytes=byte_count)


def _bundle_without_reusable_assets(
    bundle_path: Path,
    assets: list[ReusableAsset],
    local_job_dir: Path,
) -> Path:
    omitted = {asset.path for asset in assets}
    if not omitted:
        return bundle_path
    destination = local_job_dir / f"{bundle_path.stem}.remote.ffjob"
    with zipfile.ZipFile(bundle_path) as source, zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as target:
        for item in source.infolist():
            if item.filename in omitted:
                continue
            target.writestr(item, source.read(item.filename))
    return destination


def _cli_fallback_source(job_id: str, remote_bundle: str) -> str:
    return f"""
import os
import runpy
import sys

os.environ["PYTHONPATH"] = "/content/formframe/source/backend/colab"
sys.path.insert(0, "/content/formframe/source/backend/colab")
sys.argv = [
    "submit_cli.py",
    "--job-id",
    {job_id!r},
    "--bundle",
    {remote_bundle!r},
]
runpy.run_path("/content/formframe/source/backend/colab/submit_cli.py", run_name="__main__")
"""
