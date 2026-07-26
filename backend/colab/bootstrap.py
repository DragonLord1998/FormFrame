from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
import re
from urllib.parse import urlparse

ROOT = Path("/content/formframe")
SECRETS = ROOT / "secrets" / "runtime.json"
SOURCE = ROOT / "source"
STATE = ROOT / "state"
LOGS = ROOT / "logs"
VENV = ROOT / "venv"
COMFY = ROOT / "ComfyUI"
VIDEOX = ROOT / "VideoX-Fun"
CACHE = ROOT / "cache"
BOOTSTRAP_SCHEMA_VERSION = 2
COMMIT_RE = re.compile(r"^[a-fA-F0-9]{40}$")
CLOUDFLARED_VERSION = "2026.7.2"
CLOUDFLARED_ASSETS = {
    "amd64": (
        "cloudflared-linux-amd64",
        "ec905ea7b7e327ff8abdde8cb64697a2152de74dbcdbf6aec9db8364eb3886cd",
    ),
    "arm64": (
        "cloudflared-linux-arm64",
        "405df476437e027fc6d18729a5a77155c0a33a6082aeee60a799a688f3052e66",
    ),
}


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 3600,
    environment: dict[str, str] | None = None,
) -> None:
    print("[formframe]", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True, timeout=timeout, env=environment)


def output(command: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bootstrap_state(manifest: dict[str, object], status: str, **extra: object) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    model_manifest = SOURCE / "backend" / "colab" / "model-manifest.json"
    workflow = SOURCE / "comfy" / "workflows" / "controlled-character-v1.api.json"
    document = {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "status": status,
        "model_manifest_sha256": sha256(model_manifest) if model_manifest.is_file() else "",
        "workflow_sha256": sha256(workflow) if workflow.is_file() else "",
        "remote_model_cache": str(CACHE),
        "runtime_root": str(ROOT),
        "updated_at": int(time.time()),
        **extra,
    }
    (STATE / "bootstrap-status.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def require_a100() -> None:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    name = torch.cuda.get_device_name(0)
    memory = int(torch.cuda.get_device_properties(0).total_memory)
    if "A100" not in name.upper() or memory < 35 * 1024**3:
        raise RuntimeError(f"FormFrame requires A100; received {name}")


def git_environment(secrets: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    token = secrets.get("github_token", "")
    if not token:
        return environment
    askpass = ROOT / "secrets" / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "*Username*) printf '%s\\n' x-access-token ;;\n"
        "*Password*) printf '%s\\n' \"$FORMFRAME_GITHUB_TOKEN\" ;;\n"
        "*) printf '\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    environment["GIT_ASKPASS"] = str(askpass)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["FORMFRAME_GITHUB_TOKEN"] = token
    return environment


def clone_formframe_source(secrets: dict[str, str]) -> None:
    repo_url = secrets.get("github_repo_url", "").strip()
    revision = secrets.get("github_revision", "").strip()
    if not repo_url:
        raise RuntimeError("github_repo_url is required in runtime secrets")
    parsed = urlparse(repo_url)
    if parsed.username or parsed.password:
        raise RuntimeError("GitHub credentials must use runtime secrets, not the repository URL")
    if not revision or not COMMIT_RE.fullmatch(revision):
        raise RuntimeError("github_revision must be a full 40-character Git commit SHA")
    environment = git_environment(secrets)
    if not (SOURCE / ".git").is_dir():
        temporary = SOURCE.with_name("source.next")
        shutil.rmtree(temporary, ignore_errors=True)
        run(["git", "clone", "--filter=blob:none", repo_url, str(temporary)], environment=environment)
        shutil.rmtree(SOURCE, ignore_errors=True)
        temporary.replace(SOURCE)
    run(["git", "remote", "set-url", "origin", repo_url], cwd=SOURCE, environment=environment)
    run(["git", "fetch", "--depth", "1", "origin", revision], cwd=SOURCE, environment=environment)
    run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=SOURCE, environment=environment)
    actual = output(["git", "rev-parse", "HEAD"], cwd=SOURCE)
    if actual.lower() != revision.lower():
        raise RuntimeError("FormFrame source revision mismatch")
    write_bootstrap_state(
        json.loads((SOURCE / "backend" / "colab" / "model-manifest.json").read_text()),
        "source-pinned",
        github_repo_url=repo_url,
        github_revision=revision,
        github_commit=actual,
    )


def clone_exact(repository: str, revision: str, destination: Path) -> None:
    if not (destination / ".git").is_dir():
        shutil.rmtree(destination, ignore_errors=True)
        run(["git", "clone", "--filter=blob:none", repository, str(destination)])
    run(["git", "fetch", "--depth", "1", "origin", revision], cwd=destination)
    run(["git", "checkout", "--detach", revision], cwd=destination)
    actual = output(["git", "rev-parse", "HEAD"], cwd=destination)
    if actual != revision:
        raise RuntimeError(f"Revision mismatch for {repository}")


def prepare_environment() -> Path:
    python = VENV / "bin" / "python"
    if not python.is_file():
        run([sys.executable, "-m", "venv", "--system-site-packages", str(VENV)])
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "wheel"], timeout=900)
    return python


def install_sources(python: Path, manifest: dict[str, object]) -> None:
    comfy = manifest["comfyui"]
    videox = manifest["videox_fun"]
    clone_exact(comfy["repository"], comfy["revision"], COMFY)
    clone_exact(videox["repository"], videox["revision"], VIDEOX)
    run([str(python), "-m", "pip", "install", "-r", str(COMFY / "requirements.txt")], timeout=7200)
    run([str(python), "-m", "pip", "install", "-e", str(VIDEOX)], timeout=7200)
    run(
        [str(python), "-m", "pip", "install", "-r", str(SOURCE / "backend" / "colab" / "requirements.txt")],
        timeout=1800,
    )
    target = COMFY / "custom_nodes" / "VideoX-Fun"
    if target.is_symlink() or target.exists():
        if target.resolve() != VIDEOX.resolve():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    if not target.exists():
        target.symlink_to(VIDEOX, target_is_directory=True)
    formframe_nodes = COMFY / "custom_nodes" / "formframe_nodes"
    shutil.rmtree(formframe_nodes, ignore_errors=True)
    shutil.copytree(SOURCE / "comfy" / "custom_nodes" / "formframe_nodes", formframe_nodes)


def download_models(python: Path, manifest: dict[str, object]) -> None:
    cache = CACHE
    cache.mkdir(parents=True, exist_ok=True)
    script = """
import hashlib
import json
from pathlib import Path
from huggingface_hub import hf_hub_download, snapshot_download

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

manifest = json.loads(Path(%r).read_text())
comfy = Path(%r)
cache = Path(%r)
state = Path(%r)
z = manifest["z_image_turbo"]
destination = comfy / z["destination"]
destination.parent.mkdir(parents=True, exist_ok=True)
snapshot_download(
    repo_id=z["repository"],
    revision=z["revision"],
    local_dir=destination,
    cache_dir=cache / "huggingface",
)
missing = [name for name in z["required_files"] if not (destination / name).is_file()]
if missing:
    raise RuntimeError("Z-Image Turbo snapshot is incomplete: " + ", ".join(missing))
safetensors = sorted(destination.rglob("*.safetensors"))
if not safetensors:
    raise RuntimeError("Z-Image Turbo snapshot contains no safetensors checkpoints")
c = manifest["controlnet"]
target = comfy / c["destination"]
target.parent.mkdir(parents=True, exist_ok=True)
downloaded = Path(hf_hub_download(
    repo_id=c["repository"],
    revision=c["revision"],
    filename=c["filename"],
    cache_dir=cache / "huggingface",
))
if downloaded.resolve() != target.resolve():
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(downloaded)
control_sha256 = sha256(target)
if control_sha256 != c["sha256"]:
    raise RuntimeError("ControlNet SHA-256 mismatch")
state.mkdir(parents=True, exist_ok=True)
(state / "model-integrity.json").write_text(json.dumps({
    "z_image_turbo": {
        "repository": z["repository"],
        "revision": z["revision"],
        "safetensors_files": len(safetensors),
        "safetensors_bytes": sum(path.stat().st_size for path in safetensors),
    },
    "controlnet": {
        "repository": c["repository"],
        "revision": c["revision"],
        "filename": c["filename"],
        "sha256": control_sha256,
        "bytes": target.stat().st_size,
    },
}, indent=2, sort_keys=True) + "\\n")
""" % (
        str(SOURCE / "backend" / "colab" / "model-manifest.json"),
        str(COMFY),
        str(cache),
        str(STATE),
    )
    run([str(python), "-c", script], timeout=10800)
    write_bootstrap_state(manifest, "models-verified")


def install_geometry(python: Path, manifest: dict[str, object]) -> None:
    gnm = manifest["gnm"]
    gnm_root = ROOT / "gnm"
    clone_exact(gnm["repository"], gnm["revision"], gnm_root)
    asset = gnm_root / gnm["asset"]
    if not asset.is_file() or asset.stat().st_size < 10 * 1024 * 1024:
        raise RuntimeError("Pinned GNM model asset is missing or incomplete")
    run([str(python), "-m", "pip", "install", "-e", f"{gnm_root}/gnm/shape[pytorch]"], timeout=1800)
    run([str(python), "-m", "pip", "install", "smplx", "trimesh"], timeout=900)
    smplx_target = ROOT / "models" / "smplx"
    smplx_target.mkdir(parents=True, exist_ok=True)
    licensed_source = ROOT / "licensed-models" / "smplx"
    if licensed_source.is_dir():
        for path in licensed_source.glob("SMPLX_*"):
            destination = smplx_target / path.name
            if not destination.exists():
                destination.symlink_to(path)


def cloudflared() -> Path:
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"aarch64", "arm64"} else "amd64"
    asset, expected = CLOUDFLARED_ASSETS[architecture]
    destination = STATE / f"cloudflared-{CLOUDFLARED_VERSION}-{architecture}"
    if destination.is_file() and sha256(destination) == expected:
        destination.chmod(0o755)
        return destination
    temporary = destination.with_suffix(".part")
    url = (
        "https://github.com/cloudflare/cloudflared/releases/download/"
        f"{CLOUDFLARED_VERSION}/{asset}"
    )
    with urllib.request.urlopen(url, timeout=180) as response, temporary.open("wb") as output_file:
        shutil.copyfileobj(response, output_file)
    if sha256(temporary) != expected:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("cloudflared digest mismatch")
    temporary.chmod(0o755)
    temporary.replace(destination)
    return destination


def stop_process(name: str) -> None:
    pid_path = STATE / f"{name}.pid"
    if not pid_path.is_file():
        return
    try:
        pid = int(pid_path.read_text())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ValueError, OSError, ProcessLookupError):
        pass
    pid_path.unlink(missing_ok=True)


def start_process(name: str, command: list[str], environment: dict[str, str]) -> None:
    stop_process(name)
    log = (LOGS / f"{name}.log").open("ab", buffering=0)
    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
    )
    (STATE / f"{name}.pid").write_text(str(process.pid))


def wait_http(url: str, timeout_seconds: int = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(2)
    raise RuntimeError(f"Service did not become ready: {url}")


def runtime_environment(secrets: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "FORMFRAME_REMOTE_ROOT": str(ROOT),
            "FORMFRAME_COMFY_URL": "http://127.0.0.1:8188",
            "FORMFRAME_CF_ACCESS_TEAM_DOMAIN": secrets.get("access_team_domain", ""),
            "FORMFRAME_CF_ACCESS_AUDIENCE": secrets.get("access_audience", ""),
            "FORMFRAME_GATEWAY_DEVELOPMENT_TOKEN": secrets.get("development_token", ""),
            "PYTHONPATH": str(SOURCE / "backend" / "colab"),
        }
    )
    return environment


def start_services(python: Path, secrets: dict[str, str]) -> None:
    environment = runtime_environment(secrets)
    start_process(
        "comfyui",
        [
            str(python),
            str(COMFY / "main.py"),
            "--listen",
            "127.0.0.1",
            "--port",
            "8188",
            "--disable-auto-launch",
        ],
        environment,
    )
    wait_http("http://127.0.0.1:8188/system_stats", 600)
    start_process(
        "gateway",
        [
            str(python),
            "-m",
            "uvicorn",
            "formframe_gateway.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        environment,
    )
    wait_http("http://127.0.0.1:8000/openapi.json", 120)


def warmup_workflow(
    python: Path,
    manifest: dict[str, object],
    environment: dict[str, str],
) -> dict[str, object]:
    from PIL import Image

    job_id = "job_000000000000"
    inbox = ROOT / "inbox"
    outbox = ROOT / "outbox"
    work = ROOT / "work"
    for directory in (inbox, outbox, work):
        directory.mkdir(parents=True, exist_ok=True)
    output_dir = outbox / job_id
    shutil.rmtree(output_dir, ignore_errors=True)
    workup = work / "warmup"
    shutil.rmtree(workup, ignore_errors=True)
    workup.mkdir(parents=True)
    width = 768
    height = 1024
    files = {
        "rgb.webp": Image.new("RGB", (width, height), (112, 96, 88)),
        "depth.png": Image.new("RGB", (width, height), (128, 128, 128)),
        "pose.png": Image.new("RGB", (width, height), (0, 0, 0)),
    }
    payloads: dict[str, bytes] = {}
    for name, image in files.items():
        path = workup / name
        image.save(path)
        payloads[name] = path.read_bytes()
    workflow = SOURCE / "comfy" / "workflows" / "controlled-character-v1.api.json"
    controlnet = manifest["controlnet"]
    document = {
        "schema_version": 1,
        "job_id": job_id,
        "workflow": "controlled-character-v1",
        "workflow_hash": sha256(workflow),
        "character_id": "warmup",
        "project_id": "warmup",
        "width": width,
        "height": height,
        "prompt": "neutral character studio warmup",
        "negative_prompt": "",
        "seed": 1,
        "denoise": 0.45,
        "controls": {
            "depth_strength": 0.25,
            "pose_strength": 0.25,
            "normal_strength": 0,
        },
        "versions": {
            "geometry_provider": "warmup",
            "comfyui": manifest["comfyui"]["revision"],
            "videox_fun": manifest["videox_fun"]["revision"],
            "z_image_turbo": manifest["z_image_turbo"]["revision"],
            "z_image_controlnet": controlnet["revision"],
            "z_image_controlnet_sha256": controlnet["sha256"],
        },
        "assets": {
            key.split(".")[0]: {
                "path": key,
                "sha256": hashlib.sha256(value).hexdigest(),
                "bytes": len(value),
            }
            for key, value in payloads.items()
        },
        "output": {"preview_format": "webp", "final_format": "png"},
        "provider": "colab",
    }
    bundle = inbox / f"{job_id}.ffjob"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(document, sort_keys=True))
        for name, value in payloads.items():
            archive.writestr(name, value)
    run(
        [
            str(python),
            str(SOURCE / "backend" / "colab" / "submit_cli.py"),
            "--job-id",
            job_id,
            "--bundle",
            str(bundle),
        ],
        timeout=1800,
        environment=environment,
    )
    result = output_dir / "result.png"
    result_manifest = output_dir / "result.json"
    result_document = json.loads(result_manifest.read_text(encoding="utf-8"))
    observed = sha256(result)
    if result_document.get("job_id") != job_id or result_document.get("output_sha256") != observed:
        raise RuntimeError("Warmup output failed result-manifest integrity verification")
    summary = {
        "job_id": job_id,
        "result_sha256": observed,
        "result_bytes": result.stat().st_size,
        "preview_bytes": (output_dir / "preview.webp").stat().st_size,
    }
    write_bootstrap_state(manifest, "warmup-complete", warmup=summary)
    return summary


def start_tunnel(secrets: dict[str, str], environment: dict[str, str]) -> None:
    tunnel_token = secrets.get("tunnel_token", "")
    if not tunnel_token:
        raise RuntimeError("A named Cloudflare tunnel token is required")
    start_process(
        "cloudflared",
        [str(cloudflared()), "tunnel", "--no-autoupdate", "run", "--token", tunnel_token],
        environment,
    )


def main() -> int:
    for directory in (ROOT, STATE, LOGS, ROOT / "bootstrap", ROOT / "secrets"):
        directory.mkdir(parents=True, exist_ok=True)
    require_a100()
    secrets = json.loads(SECRETS.read_text()) if SECRETS.is_file() else {}
    clone_formframe_source(secrets)
    manifest = json.loads((SOURCE / "backend" / "colab" / "model-manifest.json").read_text())
    python = prepare_environment()
    install_sources(python, manifest)
    download_models(python, manifest)
    install_geometry(python, manifest)
    workflows = ROOT / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        SOURCE / "comfy" / "workflows" / "controlled-character-v1.api.json",
        workflows / "controlled-character-v1.api.json",
    )
    environment = runtime_environment(secrets)
    start_services(python, secrets)
    warmup = warmup_workflow(python, manifest, environment)
    start_tunnel(secrets, environment)
    print(
        "FORMFRAME_BOOTSTRAP_JSON:"
        + json.dumps(
            {
                "status": "ready",
                "gpu": "A100",
                "workflow": "controlled-character-v1",
                "comfyui": manifest["comfyui"]["revision"],
                "videox_fun": manifest["videox_fun"]["revision"],
                "warmup": warmup,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
