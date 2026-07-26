import asyncio
import hashlib
import importlib
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TOKEN = "test-token-" + "x" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _load_gateway(monkeypatch, tmp_path: Path, *, queue_size: int = 8):
    workflows = tmp_path / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    source_workflow = ROOT / "comfy" / "workflows" / "controlled-character-v1.api.json"
    (workflows / source_workflow.name).write_bytes(source_workflow.read_bytes())
    monkeypatch.setenv("FORMFRAME_REMOTE_ROOT", str(tmp_path))
    monkeypatch.setenv("FORMFRAME_GATEWAY_DEVELOPMENT_TOKEN", TOKEN)
    monkeypatch.setenv("FORMFRAME_CF_TUNNEL_MODE", "quick")
    monkeypatch.setenv("FORMFRAME_RUNTIME_ID", "runtime-test")
    monkeypatch.setenv("FORMFRAME_GATEWAY_MAX_QUEUE_SIZE", str(queue_size))
    module = importlib.import_module("backend.colab.formframe_gateway.app")
    module = importlib.reload(module)
    module.jobs.clear()
    return module


def _bundle(path: Path, job_id: str) -> Path:
    files = {
        "rgb.webp": b"rgb",
        "depth.png": b"depth",
        "pose.png": b"pose",
    }
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
        "workflow": "controlled-character-v1",
        "workflow_hash": hashlib.sha256(
            (ROOT / "comfy" / "workflows" / "controlled-character-v1.api.json").read_bytes()
        ).hexdigest(),
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
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, value in files.items():
            archive.writestr(name, value)
    return path


class FakeComfy:
    def __init__(self, outbox: Path):
        self.outbox = outbox
        self.interrupts = 0
        self.active_waits = 0
        self.max_active_waits = 0

    def health(self):
        return {"fake": True}

    def submit(self, bundle_path: Path) -> str:
        return f"prompt-{bundle_path.stem}"

    def wait(self, prompt_id: str):
        self.active_waits += 1
        self.max_active_waits = max(self.max_active_waits, self.active_waits)
        time.sleep(0.05)
        job_id = prompt_id.removeprefix("prompt-")
        output_dir = self.outbox / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "result.png").write_bytes(b"png")
        (output_dir / "preview.webp").write_bytes(b"webp")
        (output_dir / "result.json").write_text("{}", encoding="utf-8")
        self.active_waits -= 1
        return {"status": {"completed": True}}

    def interrupt(self):
        self.interrupts += 1


def test_gateway_health_and_content_addressed_assets(tmp_path: Path, monkeypatch):
    gateway = _load_gateway(monkeypatch, tmp_path)
    gateway.comfy = FakeComfy(gateway.settings.outbox)
    client = TestClient(gateway.app)

    health = client.get("/v1/health", headers=AUTH).json()
    assert health["runtime_id"] == "runtime-test"
    assert health["queue_size"] == 0
    assert client.get("/v1/health").status_code == 401
    assert client.get(
        "/v1/health",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/events/job_missing"):
            pass

    content = b"immutable asset"
    digest = hashlib.sha256(content).hexdigest()
    assert client.put(f"/v1/assets/{digest}", content=b"wrong", headers=AUTH).status_code == 422
    response = client.put(f"/v1/assets/{digest}", content=content, headers=AUTH)
    assert response.status_code == 201
    assert response.json() == {"sha256": digest, "bytes": len(content)}
    assert client.post("/v1/assets/check", json={"hashes": [digest]}, headers=AUTH).json() == {"missing": []}

    (tmp_path / "assets" / digest).write_bytes(b"corrupt partial upload")
    assert client.post(
        "/v1/assets/check",
        json={"hashes": [digest]},
        headers=AUTH,
    ).json() == {"missing": [digest]}


def test_gateway_queue_limit_cancel_interrupt_and_result_endpoint(tmp_path: Path, monkeypatch):
    gateway = _load_gateway(monkeypatch, tmp_path, queue_size=1)
    fake = FakeComfy(gateway.settings.outbox)
    gateway.comfy = fake
    client = TestClient(gateway.app)

    existing = gateway.RemoteJob(
        job_id="job_aaaaaaaaaaaa",
        status="queued",
        stage="Queued",
        remote_bundle=str(tmp_path / "inbox" / "job_aaaaaaaaaaaa.ffjob"),
    )
    gateway.jobs[existing.job_id] = existing
    bundle = _bundle(tmp_path / "inbox" / "job_bbbbbbbbbbbb.ffjob", "job_bbbbbbbbbbbb")
    response = client.post(
        "/v1/jobs",
        json={"job_id": "job_bbbbbbbbbbbb", "remote_bundle": str(bundle)},
        headers=AUTH,
    )
    assert response.status_code == 429

    existing.status = "rendering"
    existing.prompt_id = "prompt-job_aaaaaaaaaaaa"
    response = client.post(f"/v1/jobs/{existing.job_id}/cancel", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert fake.interrupts == 1

    output_dir = tmp_path / "outbox" / existing.job_id
    output_dir.mkdir(parents=True)
    result = output_dir / "result.png"
    result.write_bytes(b"png")
    existing.status = "completed"
    existing.result_path = str(result)
    response = client.get(f"/v1/jobs/{existing.job_id}/result", headers=AUTH)
    assert response.status_code == 200
    assert response.content == b"png"

    with client.websocket_connect(f"/v1/events?job_id={existing.job_id}", headers=AUTH) as websocket:
        assert websocket.receive_json()["job_id"] == existing.job_id


def test_gateway_runs_only_one_comfy_job_at_a_time(tmp_path: Path, monkeypatch):
    gateway = _load_gateway(monkeypatch, tmp_path, queue_size=2)
    fake = FakeComfy(gateway.settings.outbox)
    gateway.comfy = fake
    job_ids = ["job_111111111111", "job_222222222222"]
    bundles = [_bundle(tmp_path / "inbox" / f"{job_id}.ffjob", job_id) for job_id in job_ids]
    for job_id, bundle in zip(job_ids, bundles):
        gateway.jobs[job_id] = gateway.RemoteJob(
            job_id=job_id,
            status="queued",
            stage="Queued",
            remote_bundle=str(bundle),
        )

    async def run_jobs():
        await asyncio.gather(*(gateway._run(job_id, bundle) for job_id, bundle in zip(job_ids, bundles)))

    asyncio.run(run_jobs())

    assert fake.max_active_waits == 1
    assert [gateway.jobs[job_id].status for job_id in job_ids] == ["completed", "completed"]
