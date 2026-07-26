from __future__ import annotations

import hashlib
import json
import secrets
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bridge.cloudflare import CloudflareGateway, GatewayConfig, GatewayError
from bridge.colab_cli import ColabCli, ColabCliConfig, ColabCliError, require_a100
from bridge.runtime_package import RuntimeSecrets, write_runtime_secrets

from .config import FormFrameSettings

ProgressCallback = Callable[[str, str, int, str], None]
BOOTSTRAP_MARKER = "FORMFRAME_BOOTSTRAP_JSON:"
BOOTSTRAP_RUNNING_MARKER = "FORMFRAME_BOOTSTRAP_RUNNING:"
BOOTSTRAP_FAILED_MARKER = "FORMFRAME_BOOTSTRAP_FAILED:"


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
        self.development_token = ""
        if settings.cloudflare_tunnel_mode == "quick":
            self.development_token = (
                settings.gateway_development_token or secrets.token_urlsafe(48)
            )
        self.gateway_url = settings.gateway_url
        self.gateway: CloudflareGateway | None = None
        if settings.cloudflare_tunnel_mode == "managed":
            self.gateway = CloudflareGateway(
                GatewayConfig(
                    base_url=settings.gateway_url,
                    access_client_id=settings.cloudflare_client_id,
                    access_client_secret=settings.cloudflare_client_secret,
                )
            )
        self.probe: dict[str, object] = {}
        self.transfer_metrics: dict[str, Any] = {}
        self.last_bootstrap_output = ""
        self.remote_cache_dir = settings.remote_cache_dir or repo_root / "data" / "remote-cache"

    def start(self, progress: ProgressCallback) -> dict[str, Any]:
        progress("provisioning", "Provisioning A100", 8, "Reconnecting or starting formframe-a100")
        _result, session_created = self.cli.ensure_a100_session_with_ownership()
        try:
            return self._start(progress)
        except BaseException as exc:
            if self.gateway is not None:
                self.gateway.close()
            if not session_created:
                raise
            self._capture_failure_diagnostics()
            stopped = self.cli.stop()
            if stopped.returncode:
                cleanup_detail = (
                    stopped.stderr
                    or stopped.stdout
                    or f"Failed to stop Colab session {self.settings.colab_session}"
                )
                raise RemoteRuntimeError(
                    f"{exc}; automatic A100 cleanup also failed: {cleanup_detail}"
                ) from exc
            raise

    def _start(self, progress: ProgressCallback) -> dict[str, Any]:
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
                    development_token=self.development_token,
                    tunnel_mode=self.settings.cloudflare_tunnel_mode,
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
            bootstrap_output = self._run_remote_bootstrap(bootstrap, progress)
        self.last_bootstrap_output = bootstrap_output
        try:
            bootstrap_status = _parse_bootstrap_status(bootstrap_output)
        except RemoteRuntimeError as exc:
            detail = "\n".join(bootstrap_output.splitlines()[-30:])
            raise RemoteRuntimeError(
                f"{exc}; Colab CLI bootstrap output:\n{detail or 'no output'}"
            ) from exc
        if self.settings.cloudflare_tunnel_mode == "quick":
            self.gateway_url = _require_quick_tunnel_url(
                str(bootstrap_status.get("gateway_url", ""))
            )
            self.gateway = CloudflareGateway(
                GatewayConfig(
                    base_url=self.gateway_url,
                    development_token=self.development_token,
                )
            )
        progress("warming", "Checking private gateway", 92, "Waiting for the Cloudflare route")
        health = self._wait_gateway()
        progress("warming", "Benchmarking transfers", 96, "Measuring Colab CLI and Cloudflare paths")
        self.transfer_metrics = self._benchmark_transfers()
        health["transfer_metrics"] = self.transfer_metrics
        health["tunnel_mode"] = self.settings.cloudflare_tunnel_mode
        health["gateway_url"] = self.gateway_url
        if health.get("gpu") != "A100" or health.get("workflow") != "controlled-character-v1":
            raise RemoteRuntimeError("Remote gateway reported an incompatible runtime")
        return health

    def _run_remote_bootstrap(
        self,
        bootstrap: Path,
        progress: ProgressCallback,
        *,
        timeout_seconds: float = 14400,
    ) -> str:
        remote_script = "/content/formframe/bootstrap/bootstrap.py"
        remote_log = "/content/formframe/logs/bootstrap.log"
        remote_pid = "/content/formframe/state/bootstrap.pid"
        remote_metadata = "/content/formframe/state/bootstrap-run.json"
        bootstrap_sha256 = _sha256(bootstrap)
        self.cli.upload(bootstrap, remote_script, timeout_seconds=180)
        launcher = f"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

script = Path({remote_script!r})
log = Path({remote_log!r})
pid_path = Path({remote_pid!r})
metadata_path = Path({remote_metadata!r})
expected_sha256 = {bootstrap_sha256!r}
expected_revision = {self.settings.github_revision!r}
pid = 0
owned = False
if pid_path.is_file():
    try:
        pid = int(pid_path.read_text())
        os.kill(pid, 0)
        command = Path(f"/proc/{{pid}}/cmdline").read_bytes().replace(b"\\0", b" ").decode(
            "utf-8",
            errors="replace",
        )
        metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else {{}}
        owned = (
            str(script) in command
            and metadata.get("pid") == pid
            and metadata.get("bootstrap_sha256") == expected_sha256
            and metadata.get("github_revision") == expected_revision
            and metadata.get("script") == str(script)
            and metadata.get("log") == str(log)
        )
    except (ValueError, OSError, ProcessLookupError):
        pid = 0
    except json.JSONDecodeError:
        owned = False
if owned:
    print("FORMFRAME_BOOTSTRAP_LAUNCHED:reused")
else:
    if pid:
        try:
            command = Path(f"/proc/{{pid}}/cmdline").read_bytes().replace(b"\\0", b" ").decode(
                "utf-8",
                errors="replace",
            )
            if str(script) in command:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    log.parent.mkdir(parents=True, exist_ok=True)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    with log.open("ab", buffering=0) as stream:
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path.write_text(str(process.pid))
    metadata_path.write_text(json.dumps({{
        "pid": process.pid,
        "bootstrap_sha256": expected_sha256,
        "github_revision": expected_revision,
        "script": str(script),
        "log": str(log),
        "started_at": time.time(),
    }}, sort_keys=True))
    print("FORMFRAME_BOOTSTRAP_LAUNCHED:" + str(process.pid))
"""
        launched = self.cli.exec_source(
            launcher,
            "bootstrap_launch",
            timeout_seconds=120,
        )
        launch_output = "\n".join(
            part for part in (launched.stdout, launched.stderr) if part
        )
        if "FORMFRAME_BOOTSTRAP_LAUNCHED:" not in launch_output:
            raise RemoteRuntimeError(
                f"Colab did not launch the background bootstrap:\n{launch_output or 'no output'}"
            )

        poll_source = f"""
import os
from pathlib import Path

log = Path({remote_log!r})
pid_path = Path({remote_pid!r})
text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
lines = text.splitlines()
ready = next(
    (line for line in reversed(lines) if line.startswith({BOOTSTRAP_MARKER!r})),
    "",
)
if ready:
    print(ready)
else:
    pid = 0
    alive = False
    try:
        pid = int(pid_path.read_text())
        os.kill(pid, 0)
        alive = True
    except (ValueError, OSError, ProcessLookupError):
        pass
    if alive:
        print({BOOTSTRAP_RUNNING_MARKER!r} + str(len(text.encode("utf-8"))))
        print("\\n".join(lines[-30:]))
    else:
        print({BOOTSTRAP_FAILED_MARKER!r} + str(pid))
        print("\\n".join(lines[-160:]))
"""
        deadline = time.monotonic() + timeout_seconds
        attempt = 0
        while time.monotonic() < deadline:
            polled = self.cli.exec_source(
                poll_source,
                "bootstrap_poll",
                timeout_seconds=120,
            )
            output = "\n".join(
                part for part in (polled.stdout, polled.stderr) if part
            )
            if BOOTSTRAP_MARKER in output:
                return output
            if BOOTSTRAP_FAILED_MARKER in output:
                self.last_bootstrap_output = output
                raise RemoteRuntimeError(
                    f"Colab background bootstrap failed:\n{output[-32768:]}"
                )
            if BOOTSTRAP_RUNNING_MARKER not in output:
                raise RemoteRuntimeError(
                    f"Colab bootstrap poll returned an invalid response:\n{output or 'no output'}"
                )
            self.last_bootstrap_output = output
            attempt += 1
            if attempt == 1 or attempt % 6 == 0:
                log_bytes = output.rsplit(BOOTSTRAP_RUNNING_MARKER, 1)[-1].splitlines()[0]
                progress(
                    "restoring",
                    "Restoring model cache",
                    min(88, 44 + attempt // 6),
                    f"Remote bootstrap running; diagnostic log is {log_bytes} bytes",
                )
            time.sleep(10)
        raise RemoteRuntimeError(
            f"Colab background bootstrap timed out after {timeout_seconds:g}s; "
            f"last remote output:\n{self.last_bootstrap_output[-32768:] or 'no output'}"
        )

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
        gateway = self._require_gateway()
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
        cloudflare = gateway.benchmark(payload)
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
        gateway = self._require_gateway()
        last_error = ""
        for attempt in range(attempts):
            try:
                return gateway.health()
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
        gateway = self._require_gateway()
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
            gateway.submit(job_id, remote_bundle)
            def live_progress(payload: dict[str, Any]) -> None:
                remote_percent = int(payload.get("progress", 40))
                local_percent = min(92, max(72, 70 + remote_percent // 5))
                progress(local_percent, str(payload.get("stage", "Z-Image Turbo rendering")))

            remote = gateway.wait_live(
                job_id,
                timeout_seconds=1200,
                on_event=live_progress,
            )
            progress(93, "Downloading preview through Cloudflare")
            gateway.download_preview(job_id, local_job_dir / "preview.webp")
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
            self._require_gateway().cancel(job_id)
        except (GatewayError, RemoteRuntimeError):
            return

    def stop(self) -> None:
        if self.gateway is not None:
            self.gateway.close()
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
            missing = set(self._require_gateway().check_assets(hashes))
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

    def _require_gateway(self) -> CloudflareGateway:
        if self.gateway is None:
            raise RemoteRuntimeError("Cloudflare gateway has not been initialized")
        return self.gateway

    def _capture_failure_diagnostics(self) -> None:
        if not hasattr(self, "repo_root"):
            return
        destination_root = (
            self.repo_root
            / "data"
            / "validation"
            / "live-a100"
            / "failure-logs"
            / str(int(time.time()))
        )
        destination_root.mkdir(parents=True, exist_ok=True)
        if self.last_bootstrap_output:
            (destination_root / "bootstrap-cli.log").write_text(
                self.last_bootstrap_output,
                encoding="utf-8",
            )
        for name in ("bootstrap", "comfyui", "gateway", "cloudflared"):
            try:
                self.cli.download(
                    f"/content/formframe/logs/{name}.log",
                    destination_root / f"{name}.log",
                    timeout_seconds=120,
                )
            except Exception:
                continue


def _parse_bootstrap_status(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if not line.startswith(BOOTSTRAP_MARKER):
            continue
        try:
            payload = json.loads(line[len(BOOTSTRAP_MARKER) :])
        except json.JSONDecodeError as exc:
            raise RemoteRuntimeError(
                "Colab bootstrap returned malformed status JSON"
            ) from exc
        if not isinstance(payload, dict) or payload.get("status") != "ready":
            raise RemoteRuntimeError("Colab bootstrap did not report ready")
        return payload
    raise RemoteRuntimeError("Colab bootstrap status marker is missing")


def _require_quick_tunnel_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RemoteRuntimeError(
            "Colab bootstrap returned an invalid Quick Tunnel URL"
        ) from exc
    if (
        parsed.scheme != "https"
        or not hostname.endswith(".trycloudflare.com")
        or hostname == "trycloudflare.com"
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RemoteRuntimeError("Colab bootstrap returned an invalid Quick Tunnel URL")
    return f"https://{hostname}"


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
