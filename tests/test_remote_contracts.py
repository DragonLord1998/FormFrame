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
from bridge.cloudflare import CloudflareGateway, GatewayConfig, GatewayError
from bridge.colab_cli import ColabCli, ColabCliConfig, ColabCliError, _redact, parse_probe, require_a100
from bridge.runtime_package import RuntimeSecrets, build_runtime_archive, write_runtime_secrets
from services.local_controller.formframe.config import FormFrameSettings
from services.local_controller.formframe.remote import (
    ColabRemoteRuntime,
    _cli_fallback_source,
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
                development_token="test",
            )
        )


def test_cloudflare_benchmark_verifies_echo_payload():
    gateway = CloudflareGateway(
        GatewayConfig(
            base_url="https://render.example.com",
            development_token="test",
        ),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=request.content)
        ),
    )
    metrics = gateway.benchmark(b"formframe")
    assert metrics["round_trip_ms"] >= 0
    assert metrics["combined_mbps"] > 0


def _bundle(path: Path, job_id: str, *, corrupt: bool = False) -> Path:
    files = {
        "rgb.webp": b"rgb",
        "depth.png": b"depth",
        "pose.png": b"pose",
    }
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "workflow": "controlled-character-v1",
        "workflow_hash": "0" * 64,
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
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in files.items():
            archive.writestr(name, value)
    return path


def _bundle_with_reference(path: Path, job_id: str) -> tuple[Path, str, str]:
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
        "workflow_hash": "0" * 64,
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


def test_colab_bootstrap_does_not_install_or_upload_private_geometry():
    root = Path(__file__).resolve().parents[1]
    bootstrap_source = (root / "backend" / "colab" / "bootstrap.py").read_text()
    remote_source = (
        root / "services" / "local_controller" / "formframe" / "remote.py"
    ).read_text()
    assert "install_geometry(" not in bootstrap_source
    assert "licensed-models" not in remote_source
    assert "smplx-models.zip" not in remote_source


def test_fixed_workflow_runs_pose_then_depth():
    path = Path(__file__).resolve().parents[1] / "comfy" / "workflows" / "controlled-character-v1.api.json"
    workflow = json.loads(path.read_text())
    prompt = workflow["prompt"]
    assert prompt["4"]["inputs"]["control_image"] == ["1", 2]
    assert prompt["5"]["inputs"]["control_image"] == ["1", 1]
    assert prompt["5"]["inputs"]["inpaint_image"] == ["4", 0]
    assert prompt["4"]["inputs"]["mask_image"] == ["1", 10]
    assert prompt["6"]["inputs"]["job_metadata"] == ["1", 11]
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
