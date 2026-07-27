from __future__ import annotations

import hashlib
import ast
import json
import os
import zipfile
from pathlib import Path

import httpx
import pytest

from backend.colab import bootstrap
from backend.colab.formframe_gateway.bundle import validate_bundle
from backend.colab.formframe_gateway.comfy import ComfyClient, ComfyError
from bridge.cloudflare import CloudflareGateway, GatewayConfig, GatewayError
from bridge.colab_cli import ColabCli, ColabCliConfig, ColabCliError, _redact, parse_probe, require_a100
from bridge.runtime_package import RuntimeSecrets, build_runtime_archive, write_runtime_secrets
from services.local_controller.formframe.config import FormFrameSettings
from services.local_controller.formframe.remote import (
    BOOTSTRAP_MARKER,
    ColabRemoteRuntime,
    RemoteRuntimeError,
    ReusableAsset,
    _cli_fallback_source,
    _bundle_without_reusable_assets,
    _identity_lora_asset,
    _parse_bootstrap_status,
    _require_quick_tunnel_url,
    _reusable_assets,
)


def test_settings_fail_closed_without_external_configuration(monkeypatch):
    for name in list(os.environ):
        if name.startswith("FORMFRAME_"):
            monkeypatch.delenv(name, raising=False)
    settings = FormFrameSettings.from_environment()
    errors = settings.remote_readiness_errors()
    assert any("COLAB_CLI" in value for value in errors)
    assert any("SMPL-X" in value for value in errors)
    assert any("Cloudflare" in value for value in errors)
    assert any("FORMFRAME_GITHUB_REPO_URL" in value for value in errors)
    assert any("FORMFRAME_GITHUB_REVISION" in value for value in errors)


def test_quick_tunnel_mode_needs_no_cloudflare_account_configuration(
    monkeypatch,
    tmp_path: Path,
):
    executable = tmp_path / "colab"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    smplx = tmp_path / "smplx"
    smplx.mkdir()
    (smplx / "SMPLX_NEUTRAL.npz").write_bytes(b"model")
    gnm = tmp_path / "gnm"
    gnm_model = gnm / "gnm" / "shape" / "data" / "versions" / "v3_0"
    gnm_model.mkdir(parents=True)
    (gnm_model / "gnm_head.npz").write_bytes(b"model")
    values = {
        "FORMFRAME_COLAB_CLI": str(executable),
        "FORMFRAME_COLAB_GPU": "A100",
        "FORMFRAME_CF_TUNNEL_MODE": "quick",
        "FORMFRAME_GITHUB_REPO_URL": "https://github.com/example/formframe.git",
        "FORMFRAME_GITHUB_REVISION": "0123456789abcdef0123456789abcdef01234567",
        "FORMFRAME_SMPLX_MODEL_DIR": str(smplx),
        "FORMFRAME_GNM_CHECKOUT": str(gnm),
    }
    for name in list(os.environ):
        if name.startswith("FORMFRAME_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = FormFrameSettings.from_environment()

    assert settings.remote_readiness_errors() == []
    runtime = ColabRemoteRuntime(settings, Path(__file__).resolve().parents[1])
    assert runtime.gateway is None
    assert len(runtime.development_token) >= 48


def test_colab_probe_requires_actual_a100():
    payload = parse_probe(
        'noise\nFORMFRAME_PROBE_JSON:{"cuda_available":true,"gpu":"NVIDIA A100-SXM4-40GB",'
        '"vram_bytes":42949672960}\n'
    )
    require_a100(payload)
    with pytest.raises(ColabCliError):
        require_a100({"cuda_available": True, "gpu": "T4", "vram_bytes": 16 * 1024**3})


def test_colab_cli_uses_argument_array_and_exact_session(tmp_path: Path):
    executable = tmp_path / "colab"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    executable.chmod(0o755)
    cli = ColabCli(
        ColabCliConfig(
            executable=executable,
            session_name="formframe-a100",
            gpu="A100",
            auth_provider="adc",
        )
    )
    result = cli.status()
    assert result.returncode == 0
    assert result.stdout.splitlines() == ["--auth", "adc", "status", "-s", "formframe-a100"]

    stopped = cli.stop()
    assert stopped.returncode == 0
    assert stopped.stdout.splitlines() == [
        "--auth",
        "adc",
        "stop",
        "-s",
        "formframe-a100",
    ]


def test_colab_cli_creates_session_when_status_returns_zero_but_reports_not_found(
    tmp_path: Path,
):
    executable = tmp_path / "colab"
    executable.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *\"status -s formframe-a100\"*)\n"
        "    echo \"[colab] Session 'formframe-a100' not found.\"\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "printf '%s\\n' \"$@\"\n"
    )
    executable.chmod(0o755)
    cli = ColabCli(
        ColabCliConfig(
            executable=executable,
            session_name="formframe-a100",
            gpu="A100",
            auth_provider="oauth2",
        )
    )

    result, created = cli.ensure_a100_session_with_ownership()

    assert created is True
    assert result.stdout.splitlines() == [
        "--auth",
        "oauth2",
        "new",
        "-s",
        "formframe-a100",
        "--gpu",
        "A100",
    ]


@pytest.mark.parametrize(
    ("session_created", "expected_stop_calls"),
    [(True, 1), (False, 0)],
)
def test_remote_start_failure_stops_only_a100_created_by_that_start(
    session_created: bool,
    expected_stop_calls: int,
):
    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class FakeCli:
        def __init__(self):
            self.stop_calls = 0

        def ensure_a100_session_with_ownership(self):
            return FakeResult(), session_created

        def stop(self):
            self.stop_calls += 1
            return FakeResult()

    runtime = object.__new__(ColabRemoteRuntime)
    runtime.cli = FakeCli()
    runtime.gateway = None
    runtime.settings = type(
        "Settings",
        (),
        {"colab_session": "formframe-a100"},
    )()
    runtime._start = lambda _progress: (_ for _ in ()).throw(
        RemoteRuntimeError("bootstrap failed")
    )

    with pytest.raises(RemoteRuntimeError, match="bootstrap failed"):
        runtime.start(lambda *_args: None)

    assert runtime.cli.stop_calls == expected_stop_calls


def test_remote_start_interrupt_stops_a100_created_by_that_start():
    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class FakeCli:
        stop_calls = 0

        def ensure_a100_session_with_ownership(self):
            return FakeResult(), True

        def stop(self):
            self.stop_calls += 1
            return FakeResult()

    runtime = object.__new__(ColabRemoteRuntime)
    runtime.cli = FakeCli()
    runtime.gateway = None
    runtime.settings = type(
        "Settings",
        (),
        {"colab_session": "formframe-a100"},
    )()
    runtime._start = lambda _progress: (_ for _ in ()).throw(KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        runtime.start(lambda *_args: None)

    assert runtime.cli.stop_calls == 1


def test_remote_bootstrap_runs_in_background_and_is_polled(
    monkeypatch,
    tmp_path: Path,
):
    class FakeResult:
        def __init__(self, stdout: str):
            self.stdout = stdout
            self.stderr = ""

    class FakeCli:
        def __init__(self):
            self.polls = 0
            self.uploads = []
            self.sources = {}

        def upload(self, source, destination, **_kwargs):
            self.uploads.append((Path(source), destination))

        def exec_source(self, source, label, **_kwargs):
            self.sources[label] = source
            if label == "bootstrap_launch":
                return FakeResult("FORMFRAME_BOOTSTRAP_LAUNCHED:123")
            self.polls += 1
            if self.polls == 1:
                return FakeResult("FORMFRAME_BOOTSTRAP_RUNNING:4096")
            return FakeResult(
                BOOTSTRAP_MARKER
                + json.dumps({"status": "ready", "gateway_url": "https://x.trycloudflare.com"})
            )

    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text("print('bootstrap')\n")
    runtime = object.__new__(ColabRemoteRuntime)
    runtime.cli = FakeCli()
    runtime.settings = type(
        "Settings",
        (),
        {"github_revision": "0" * 40},
    )()
    runtime.last_bootstrap_output = ""
    progress_events = []
    monkeypatch.setattr(
        "services.local_controller.formframe.remote.time.sleep",
        lambda _seconds: None,
    )

    output = runtime._run_remote_bootstrap(
        bootstrap,
        lambda *event: progress_events.append(event),
        timeout_seconds=30,
    )

    assert BOOTSTRAP_MARKER in output
    assert runtime.cli.uploads == [
        (bootstrap, "/content/formframe/bootstrap/bootstrap.py")
    ]
    assert runtime.cli.polls == 2
    assert progress_events[0][0] == "restoring"
    assert "4096 bytes" in progress_events[0][3]
    launcher = runtime.cli.sources["bootstrap_launch"]
    assert "bootstrap-run.json" in launcher
    assert hashlib.sha256(bootstrap.read_bytes()).hexdigest() in launcher
    assert "0" * 40 in launcher
    assert "/proc/{pid}/cmdline" in launcher
    poller = runtime.cli.sources["bootstrap_poll"]
    assert 'state not in {"Z", "X"}' in poller
    assert 'str(script) in command' in poller


def test_remote_bootstrap_poll_surfaces_remote_log_tail(
    monkeypatch,
    tmp_path: Path,
):
    class FakeResult:
        stderr = ""

        def __init__(self, stdout: str):
            self.stdout = stdout

    class FakeCli:
        def upload(self, *_args, **_kwargs):
            return None

        def exec_source(self, _source, label, **_kwargs):
            if label == "bootstrap_launch":
                return FakeResult("FORMFRAME_BOOTSTRAP_LAUNCHED:123")
            return FakeResult(
                "FORMFRAME_BOOTSTRAP_FAILED:123\n"
                "ComfyUI rejected the pinned workflow"
            )

    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text("print('bootstrap')\n")
    runtime = object.__new__(ColabRemoteRuntime)
    runtime.cli = FakeCli()
    runtime.settings = type(
        "Settings",
        (),
        {"github_revision": "0" * 40},
    )()
    runtime.last_bootstrap_output = ""
    monkeypatch.setattr(
        "services.local_controller.formframe.remote.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(
        RemoteRuntimeError,
        match="ComfyUI rejected the pinned workflow",
    ):
        runtime._run_remote_bootstrap(
            bootstrap,
            lambda *_event: None,
            timeout_seconds=30,
        )

    assert "FORMFRAME_BOOTSTRAP_FAILED" in runtime.last_bootstrap_output


def test_remote_bootstrap_timeout_preserves_last_log_tail(
    monkeypatch,
    tmp_path: Path,
):
    class FakeResult:
        stderr = ""

        def __init__(self, stdout: str):
            self.stdout = stdout

    class FakeCli:
        def upload(self, *_args, **_kwargs):
            return None

        def exec_source(self, _source, label, **_kwargs):
            if label == "bootstrap_launch":
                return FakeResult("FORMFRAME_BOOTSTRAP_LAUNCHED:123")
            return FakeResult(
                "FORMFRAME_BOOTSTRAP_RUNNING:8192\n"
                "Downloading pinned Z-Image shard 4"
            )

    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text("print('bootstrap')\n")
    runtime = object.__new__(ColabRemoteRuntime)
    runtime.cli = FakeCli()
    runtime.last_bootstrap_output = ""
    runtime.settings = type(
        "Settings",
        (),
        {"github_revision": "0" * 40},
    )()
    clock = iter((0.0, 0.0, 31.0))
    monkeypatch.setattr(
        "services.local_controller.formframe.remote.time.monotonic",
        lambda: next(clock),
    )
    monkeypatch.setattr(
        "services.local_controller.formframe.remote.time.sleep",
        lambda _seconds: None,
    )

    with pytest.raises(
        RemoteRuntimeError,
        match="Downloading pinned Z-Image shard 4",
    ):
        runtime._run_remote_bootstrap(
            bootstrap,
            lambda *_event: None,
            timeout_seconds=30,
        )

    assert "Downloading pinned Z-Image shard 4" in runtime.last_bootstrap_output


def test_colab_cli_redacts_token_flags():
    assert _redact("cloudflared run --token sensitive-value") == (
        "cloudflared run --token [REDACTED]"
    )


def test_cloudflare_gateway_sends_service_token_and_only_metadata():
    observed = {}

    def handler(request: httpx.Request):
        observed["headers"] = request.headers
        observed["json"] = json.loads(request.content)
        return httpx.Response(202, json={"job_id": "job_123456789abc", "status": "queued"})

    gateway = CloudflareGateway(
        GatewayConfig(
            base_url="https://render.example.com",
            access_client_id="client-id",
            access_client_secret="client-secret",
        ),
        transport=httpx.MockTransport(handler),
    )
    payload = gateway.submit(
        "job_123456789abc",
        "/content/formframe/inbox/job_123456789abc.ffjob",
    )
    assert payload["status"] == "queued"
    assert observed["headers"]["CF-Access-Client-Id"] == "client-id"
    assert observed["headers"]["CF-Access-Client-Secret"] == "client-secret"
    assert set(observed["json"]) == {"job_id", "remote_bundle"}


def test_remote_gateway_rejects_plain_http():
    with pytest.raises(GatewayError):
        CloudflareGateway(
            GatewayConfig(
                base_url="http://render.example.com",
                development_token="x" * 48,
            )
        )


def test_cloudflare_benchmark_verifies_echo_payload():
    gateway = CloudflareGateway(
        GatewayConfig(
            base_url="https://render.example.com",
            development_token="x" * 48,
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=request.content)
        ),
    )
    metrics = gateway.benchmark(b"formframe")
    assert metrics["round_trip_ms"] >= 0
    assert metrics["combined_mbps"] > 0


def _bundle(
    path: Path,
    job_id: str,
    *,
    corrupt: bool = False,
    identity_lora: bytes | None = None,
    workflow_hash: str | None = None,
) -> Path:
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )
    files = {
        "rgb.webp": b"rgb",
        "depth.png": b"depth",
        "pose.png": b"pose",
    }
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "workflow": "controlled-character-v1",
        "workflow_hash": workflow_hash or hashlib.sha256(workflow_path.read_bytes()).hexdigest(),
        "character_id": "character_test",
        "project_id": "project_test",
        "width": 768,
        "height": 1024,
        "prompt": "portrait",
        "negative_prompt": "",
        "seed": 1,
        "denoise": 0.5,
        "controls": {"depth_strength": 0.7, "pose_strength": 0.4},
        "versions": {},
        "assets": {
            key.split(".")[0]: {
                "path": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "bytes": len(value),
            }
            for key, value in files.items()
        },
        "output": {},
        "provider": "colab",
    }
    if corrupt:
        manifest["assets"]["pose"]["sha256"] = "0" * 64
    if identity_lora is not None:
        digest = hashlib.sha256(identity_lora).hexdigest()
        manifest["assets"]["identity_lora"] = {
            "path": f"formframe_{digest}.safetensors",
            "sha256": digest,
            "bytes": len(identity_lora),
        }
        manifest["controls"].update(
            {
                "identity_mode": "trained-lora",
                "identity_lora_strength": 0.85,
                "identity_trigger_token": "ff_mara",
            }
        )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in files.items():
            archive.writestr(name, value)
    return path


def _bundle_with_reference(path: Path, job_id: str) -> tuple[Path, str, str]:
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )
    files = {
        "rgb.webp": b"rgb",
        "depth.png": b"depth",
        "pose.png": b"pose",
    }
    reference = b"reference image"
    digest = hashlib.sha256(reference).hexdigest()
    cached_bytes = b"already cached"
    cached = hashlib.sha256(cached_bytes).hexdigest()
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "workflow": "controlled-character-v1",
        "workflow_hash": hashlib.sha256(workflow_path.read_bytes()).hexdigest(),
        "character_id": "character_test",
        "project_id": "project_test",
        "width": 768,
        "height": 1024,
        "prompt": "portrait",
        "negative_prompt": "",
        "seed": 1,
        "denoise": 0.5,
        "controls": {"depth_strength": 0.7, "pose_strength": 0.4},
        "versions": {},
        "assets": {
            key.split(".")[0]: {
                "path": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "bytes": len(value),
            }
            for key, value in files.items()
        },
        "output": {},
        "provider": "colab",
    }
    manifest["assets"]["references"] = [
        {
            "role": "face_front",
            "path": f"ref_face_front_{digest[:12]}.webp",
            "sha256": digest,
            "bytes": len(reference),
        },
        {
            "role": "outfit",
            "path": f"ref_outfit_{cached[:12]}.webp",
            "sha256": cached,
            "bytes": len(cached_bytes),
        },
    ]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in files.items():
            archive.writestr(name, value)
        archive.writestr(f"ref_face_front_{digest[:12]}.webp", reference)
        archive.writestr(f"ref_outfit_{cached[:12]}.webp", cached_bytes)
    return path, digest, cached


def test_gateway_bundle_validation_verifies_fixed_workflow_and_hashes(tmp_path: Path):
    job_id = "job_123456789abc"
    validated = validate_bundle(_bundle(tmp_path / "valid.ffjob", job_id), job_id)
    assert validated.manifest["workflow"] == "controlled-character-v1"
    with pytest.raises(ValueError, match="pose hash mismatch"):
        validate_bundle(_bundle(tmp_path / "bad.ffjob", job_id, corrupt=True), job_id)
    mismatched = _bundle(
        tmp_path / "workflow-mismatch.ffjob",
        job_id,
        workflow_hash="0" * 64,
    )
    with pytest.raises(ValueError, match="does not match the pinned workflow"):
        validate_bundle(mismatched, job_id)


def test_trained_identity_lora_is_validated_and_patched_into_pinned_workflow(tmp_path: Path):
    job_id = "job_123456789abc"
    lora = b"trained identity lora"
    digest = hashlib.sha256(lora).hexdigest()
    bundle = _bundle(
        tmp_path / "identity.ffjob",
        job_id,
        identity_lora=lora,
    )
    validated = validate_bundle(bundle, job_id)
    assert validated.manifest["assets"]["identity_lora"]["sha256"] == digest
    assert _identity_lora_asset(bundle) == ReusableAsset(
        path=f"formframe_{digest}.safetensors",
        sha256=digest,
        bytes=len(lora),
    )
    lora_root = tmp_path / "loras"
    lora_root.mkdir()
    (lora_root / f"formframe_{digest}.safetensors").write_bytes(lora)
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )
    prompt = json.loads(workflow_path.read_text())["prompt"]
    client = ComfyClient("http://127.0.0.1:8188", workflow_path, lora_root)

    client._configure_identity_lora(prompt, bundle)

    assert prompt["7"]["class_type"] == "LoadZImageLora"
    assert prompt["7"]["inputs"]["lora_name"] == f"formframe_{digest}.safetensors"
    assert prompt["7"]["inputs"]["strength_model"] == 0.85
    assert prompt["3"]["inputs"]["funmodels"] == ["7", 0]


def test_comfy_rehashes_identity_lora_even_when_stale_sidecar_matches(tmp_path: Path):
    job_id = "job_123456789abc"
    original = b"trained identity lora"
    corrupt = b"corrupt identity lora"
    assert len(original) == len(corrupt)
    digest = hashlib.sha256(original).hexdigest()
    bundle = _bundle(tmp_path / "identity.ffjob", job_id, identity_lora=original)
    lora_root = tmp_path / "loras"
    lora_root.mkdir()
    lora_path = lora_root / f"formframe_{digest}.safetensors"
    lora_path.write_bytes(corrupt)
    lora_path.with_name(f"{lora_path.name}.sha256").write_text(digest + "\n")
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )
    prompt = json.loads(workflow_path.read_text())["prompt"]
    client = ComfyClient("http://127.0.0.1:8188", workflow_path, lora_root)

    with pytest.raises(ComfyError, match="failed its SHA-256 check"):
        client._configure_identity_lora(prompt, bundle)


def test_workflow_bypasses_identity_lora_node_when_none_is_attached(tmp_path: Path):
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )
    prompt = json.loads(workflow_path.read_text())["prompt"]
    bundle = _bundle(tmp_path / "plain.ffjob", "job_123456789abc")
    client = ComfyClient("http://127.0.0.1:8188", workflow_path, tmp_path / "loras")

    client._configure_identity_lora(prompt, bundle)

    assert "7" not in prompt
    assert prompt["3"]["inputs"]["funmodels"] == ["2", 0]


def test_comfy_submit_surfaces_workflow_validation_response(tmp_path: Path):
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )
    bundle = _bundle(tmp_path / "plain.ffjob", "job_123456789abc")

    def handler(_request: httpx.Request):
        return httpx.Response(
            400,
            json={"error": {"message": "Unknown node LoadZImageModel"}},
        )

    client = ComfyClient("http://127.0.0.1:8188", workflow_path)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://127.0.0.1:8188",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ComfyError, match="Unknown node LoadZImageModel"):
        client.submit(bundle)


def test_reusable_asset_negotiation_uploads_only_gateway_misses(tmp_path: Path):
    bundle, missing_digest, cached_digest = _bundle_with_reference(
        tmp_path / "refs.ffjob",
        "job_123456789abc",
    )

    class FakeGateway:
        def check_assets(self, hashes):
            assert set(hashes) == {missing_digest, cached_digest}
            return [missing_digest]

    class FakeCli:
        def __init__(self):
            self.uploads = []

        def upload(self, local_path, remote_path, **_kwargs):
            self.uploads.append((Path(local_path).read_bytes(), remote_path))

    runtime = object.__new__(ColabRemoteRuntime)
    runtime.gateway = FakeGateway()
    runtime.cli = FakeCli()
    plan = runtime._stage_reusable_assets(bundle, _reusable_assets(bundle))
    assert plan["asset_cache"] == "content-addressed"
    assert plan["asset_count"] == 2
    assert plan["missing_count"] == 1
    assert plan["uploaded_count"] == 1
    assert runtime.cli.uploads == [(b"reference image", f"/content/formframe/assets/{missing_digest}")]


def test_remote_bundle_omits_reusable_references_after_hash_staging(tmp_path: Path):
    bundle, missing_digest, cached_digest = _bundle_with_reference(
        tmp_path / "refs.ffjob",
        "job_123456789abc",
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    optimized = _bundle_without_reusable_assets(
        bundle,
        _reusable_assets(bundle),
        job_dir,
    )

    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(optimized) as remote:
        assert {
            f"ref_face_front_{missing_digest[:12]}.webp",
            f"ref_outfit_{cached_digest[:12]}.webp",
        } <= set(source.namelist())
        assert not any(name.startswith("ref_") for name in remote.namelist())
        assert json.loads(remote.read("manifest.json")) == json.loads(
            source.read("manifest.json")
        )


def test_gateway_validates_omitted_reference_against_remote_hash_cache(tmp_path: Path):
    bundle, missing_digest, _cached_digest = _bundle_with_reference(
        tmp_path / "refs.ffjob",
        "job_123456789abc",
    )
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    assets = _reusable_assets(bundle)
    optimized = _bundle_without_reusable_assets(bundle, assets, job_dir)
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    with zipfile.ZipFile(bundle) as archive:
        for asset in assets:
            (asset_root / asset.sha256).write_bytes(archive.read(asset.path))
    workflow_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "workflows"
        / "controlled-character-v1.api.json"
    )

    validated = validate_bundle(
        optimized,
        "job_123456789abc",
        workflow_path,
        asset_root,
    )

    assert validated.manifest["assets"]["references"][0]["sha256"] == missing_digest
    (asset_root / missing_digest).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="reference hash mismatch"):
        validate_bundle(
            optimized,
            "job_123456789abc",
            workflow_path,
            asset_root,
        )


def test_identity_lora_uses_cli_bulk_transfer_and_remote_hash_cache(tmp_path: Path):
    content = b"trained identity lora"
    digest = hashlib.sha256(content).hexdigest()
    data_root = tmp_path / "data"
    local_job_dir = data_root / "jobs" / "job_123456789abc"
    local_job_dir.mkdir(parents=True)
    assets = data_root / "assets"
    assets.mkdir()
    (assets / digest).write_bytes(content)
    asset = ReusableAsset(
        path=f"formframe_{digest}.safetensors",
        sha256=digest,
        bytes=len(content),
    )

    class FakeResult:
        def __init__(self, stdout):
            self.stdout = stdout

    class FakeCli:
        def __init__(self):
            self.uploads = []
            self.exec_names = []

        def exec_source(self, _source, name, **_kwargs):
            self.exec_names.append(name)
            if "probe" in name:
                return FakeResult("FORMFRAME_LORA_CACHE:miss")
            if "verify" in name:
                return FakeResult("FORMFRAME_LORA_CACHE:verified")
            return FakeResult("")

        def upload(self, local_path, remote_path, **_kwargs):
            self.uploads.append((Path(local_path).read_bytes(), remote_path))

    runtime = object.__new__(ColabRemoteRuntime)
    runtime.cli = FakeCli()
    plan = runtime._stage_identity_lora(asset, local_job_dir)

    assert plan["bulk_route"] == "colab-cli"
    assert plan["cache_hit"] is False
    assert plan["uploaded"] is True
    assert runtime.cli.uploads == [
        (
            content,
            f"/content/formframe/ComfyUI/models/loras/formframe_{digest}.safetensors",
        )
    ]
    assert any("verify" in name for name in runtime.cli.exec_names)


def test_remote_render_downloads_final_png_with_cli_even_after_cloudflare_completion(tmp_path: Path):
    bundle, _missing_digest, _cached_digest = _bundle_with_reference(
        tmp_path / "render.ffjob",
        "job_123456789abc",
    )
    job_dir = tmp_path / "job"

    class FakeGateway:
        def check_assets(self, hashes):
            return []

        def submit(self, job_id, remote_bundle):
            return {"job_id": job_id, "status": "queued", "remote_bundle": remote_bundle}

        def wait_live(self, job_id, **_kwargs):
            return {"job_id": job_id, "status": "completed"}

        def download_preview(self, job_id, destination):
            destination.write_bytes(b"preview")

    class FakeCli:
        def __init__(self):
            self.uploads = []
            self.downloads = []

        def upload(self, local_path, remote_path, **_kwargs):
            self.uploads.append((Path(local_path).name, remote_path))

        def download(self, remote_path, local_path, **_kwargs):
            self.downloads.append((remote_path, Path(local_path).name))
            if remote_path.endswith("result.png"):
                Path(local_path).write_bytes(b"png")
            elif remote_path.endswith("result.json"):
                Path(local_path).write_text(
                    json.dumps(
                        {
                            "job_id": "job_123456789abc",
                            "output_sha256": hashlib.sha256(b"png").hexdigest(),
                        }
                    ),
                    encoding="utf-8",
                )

    runtime = object.__new__(ColabRemoteRuntime)
    runtime.gateway = FakeGateway()
    runtime.cli = FakeCli()
    result = runtime.render("job_123456789abc", bundle, job_dir, lambda *_args: None)
    assert result.used_cli_fallback is False
    assert (job_dir / "preview.webp").read_bytes() == b"preview"
    assert (job_dir / "result.png").read_bytes() == b"png"
    assert (
        "/content/formframe/outbox/job_123456789abc/result.json",
        "result.json",
    ) in runtime.cli.downloads


def test_runtime_package_contains_code_but_secrets_are_separate(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    archive = build_runtime_archive(repo_root, tmp_path / "runtime.zip")
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert "backend/colab/bootstrap.py" in names
    assert "comfy/workflows/controlled-character-v1.api.json" in names
    assert not any("secret" in name.lower() for name in names)
    secret_path = write_runtime_secrets(
        RuntimeSecrets(
            tunnel_token="tunnel",
            access_team_domain="team.cloudflareaccess.com",
            access_audience="audience",
            github_repo_url="https://github.com/example/formframe.git",
            github_revision="0123456789abcdef0123456789abcdef01234567",
        ),
        tmp_path / "runtime.json",
    )
    assert secret_path.stat().st_mode & 0o777 == 0o600
    document = json.loads(secret_path.read_text(encoding="utf-8"))
    assert document["github_repo_url"] == "https://github.com/example/formframe.git"
    assert document["github_revision"] == "0123456789abcdef0123456789abcdef01234567"
    assert document["github_token"] == ""


def test_quick_tunnel_runtime_secrets_need_only_ephemeral_bearer_auth(
    tmp_path: Path,
):
    secret_path = write_runtime_secrets(
        RuntimeSecrets(
            tunnel_token="",
            access_team_domain="",
            access_audience="",
            development_token="ephemeral-bearer-token-" + "x" * 32,
            tunnel_mode="quick",
            github_repo_url="https://github.com/example/formframe.git",
            github_revision="0123456789abcdef0123456789abcdef01234567",
        ),
        tmp_path / "runtime.json",
    )
    document = json.loads(secret_path.read_text(encoding="utf-8"))
    assert document["tunnel_mode"] == "quick"
    assert document["tunnel_token"] == ""
    assert document["development_token"] == "ephemeral-bearer-token-" + "x" * 32


def test_cli_fallback_executes_the_pinned_github_checkout():
    source = _cli_fallback_source(
        "job_123456789abc",
        "/content/formframe/inbox/job_123456789abc.ffjob",
    )
    assert "/content/formframe/source/backend/colab/submit_cli.py" in source
    assert "/content/formframe/runtime/" not in source


def test_cloudflared_token_is_passed_only_through_environment(monkeypatch, tmp_path: Path):
    observed = {}
    executable = tmp_path / "cloudflared"
    executable.write_text("")

    def fake_start_process(name, command, environment):
        observed.update(name=name, command=command, environment=environment)

    monkeypatch.setattr(bootstrap, "cloudflared", lambda: executable)
    monkeypatch.setattr(bootstrap, "start_process", fake_start_process)
    monkeypatch.setattr(bootstrap, "wait_tunnel_connected", lambda: None)
    bootstrap.start_tunnel({"tunnel_token": "super-secret"}, {"SAFE": "value"})
    assert observed["name"] == "cloudflared"
    assert "super-secret" not in observed["command"]
    assert "--token" not in observed["command"]
    assert observed["environment"]["TUNNEL_TOKEN"] == "super-secret"
    assert observed["environment"]["SAFE"] == "value"


def test_cloudflared_quick_tunnel_needs_no_account_token(monkeypatch, tmp_path: Path):
    observed = {}
    executable = tmp_path / "cloudflared"
    executable.write_text("")
    logs = tmp_path / "logs"
    state = tmp_path / "state"
    logs.mkdir()
    state.mkdir()

    def fake_start_process(name, command, environment):
        observed.update(name=name, command=command, environment=environment)

    monkeypatch.setattr(bootstrap, "LOGS", logs)
    monkeypatch.setattr(bootstrap, "STATE", state)
    monkeypatch.setattr(bootstrap, "cloudflared", lambda: executable)
    monkeypatch.setattr(bootstrap, "start_process", fake_start_process)
    monkeypatch.setattr(
        bootstrap,
        "wait_quick_tunnel_url",
        lambda: "https://formframe-test.trycloudflare.com",
    )

    gateway_url = bootstrap.start_tunnel(
        {"tunnel_mode": "quick", "development_token": "x" * 48},
        {"SAFE": "value"},
    )

    assert gateway_url == "https://formframe-test.trycloudflare.com"
    assert observed["name"] == "cloudflared"
    assert observed["command"] == [
        str(executable),
        "tunnel",
        "--no-autoupdate",
        "--url",
        "http://127.0.0.1:8000",
    ]
    assert "TUNNEL_TOKEN" not in observed["environment"]
    assert (state / "gateway-url.txt").read_text().strip() == gateway_url


def test_tunnel_readiness_requires_registered_connection(monkeypatch, tmp_path: Path):
    logs = tmp_path / "logs"
    state = tmp_path / "state"
    logs.mkdir()
    state.mkdir()
    (logs / "cloudflared.log").write_text(
        "INF Registered tunnel connection connIndex=0\n",
        encoding="utf-8",
    )
    (state / "cloudflared.pid").write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "LOGS", logs)
    monkeypatch.setattr(bootstrap, "STATE", state)
    bootstrap.wait_tunnel_connected(timeout_seconds=1)


def test_quick_tunnel_url_is_discovered_from_live_cloudflared_log(
    monkeypatch,
    tmp_path: Path,
):
    logs = tmp_path / "logs"
    state = tmp_path / "state"
    logs.mkdir()
    state.mkdir()
    (logs / "cloudflared.log").write_text(
        "INF Your quick Tunnel has been created! Visit it at "
        "https://amber-frame-test.trycloudflare.com\n",
        encoding="utf-8",
    )
    (state / "cloudflared.pid").write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "LOGS", logs)
    monkeypatch.setattr(bootstrap, "STATE", state)

    assert bootstrap.wait_quick_tunnel_url(timeout_seconds=1) == (
        "https://amber-frame-test.trycloudflare.com"
    )


def test_quick_tunnel_bootstrap_status_requires_exact_cloudflare_hostname():
    payload = _parse_bootstrap_status(
        "noise\n"
        + BOOTSTRAP_MARKER
        + json.dumps(
            {
                "status": "ready",
                "gateway_url": "https://amber-frame-test.trycloudflare.com",
            }
        )
    )
    assert _require_quick_tunnel_url(payload["gateway_url"]) == (
        "https://amber-frame-test.trycloudflare.com"
    )
    with pytest.raises(RemoteRuntimeError, match="invalid Quick Tunnel URL"):
        _require_quick_tunnel_url("https://trycloudflare.com.attacker.example")
    with pytest.raises(RemoteRuntimeError, match="invalid Quick Tunnel URL"):
        _require_quick_tunnel_url(
            "https://amber-frame-test.trycloudflare.com:not-a-port"
        )


def test_colab_bootstrap_does_not_install_or_upload_private_geometry():
    root = Path(__file__).resolve().parents[1]
    bootstrap_source = (root / "backend" / "colab" / "bootstrap.py").read_text()
    remote_source = (
        root / "services" / "local_controller" / "formframe" / "remote.py"
    ).read_text()
    assert "install_geometry(" not in bootstrap_source
    assert "licensed-models" not in remote_source
    assert "smplx-models.zip" not in remote_source


def test_colab_bootstrap_works_without_ensurepip_on_current_runtime():
    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "colab"
        / "bootstrap.py"
    ).read_text()
    assert '"--without-pip"' in source
    assert 'os.environ.setdefault("HF_HUB_DISABLE_XET", "1")' in source
    assert source.index('os.environ.setdefault("HF_HUB_DISABLE_XET", "1")') < (
        source.index("from huggingface_hub import")
    )


def test_colab_bootstrap_installs_videox_custom_node_requirements():
    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "colab"
        / "bootstrap.py"
    ).read_text()
    requirements = 'str(VIDEOX / "requirements.txt")'
    editable = 'str(VIDEOX)'
    assert requirements in source
    assert source.index(requirements) < source.index(editable, source.index(requirements))


def test_fixed_workflow_runs_depth_then_pose():
    path = Path(__file__).resolve().parents[1] / "comfy" / "workflows" / "controlled-character-v1.api.json"
    workflow = json.loads(path.read_text())
    prompt = workflow["prompt"]
    assert prompt["4"]["inputs"]["control_image"] == ["1", 1]
    assert prompt["4"]["inputs"]["control_context_scale"] == ["1", 8]
    assert prompt["5"]["inputs"]["control_image"] == ["1", 2]
    assert prompt["5"]["inputs"]["control_context_scale"] == ["1", 9]
    assert prompt["5"]["inputs"]["inpaint_image"] == ["4", 0]
    assert prompt["4"]["inputs"]["mask_image"] == ["1", 10]
    assert prompt["6"]["inputs"]["job_metadata"] == ["1", 11]
    assert prompt["7"]["class_type"] == "LoadZImageLora"
    assert prompt["7"]["inputs"]["funmodels"] == ["2", 0]
    assert prompt["3"]["inputs"]["funmodels"] == ["7", 0]
    nodes_path = (
        Path(__file__).resolve().parents[1]
        / "comfy"
        / "custom_nodes"
        / "formframe_nodes"
        / "nodes.py"
    )
    tree = ast.parse(nodes_path.read_text())
    loader = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FormFrameJobLoader")
    return_types = next(
        ast.literal_eval(node.value)
        for node in loader.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "RETURN_TYPES" for target in node.targets)
    )
    assert return_types[10] == "IMAGE"
