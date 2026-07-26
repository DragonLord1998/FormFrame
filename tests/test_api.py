import asyncio
import hashlib
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from services.local_controller.formframe.app import app, runtime, store
from services.local_controller.formframe.models import Project


def test_gateway_health_and_asset_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "assets_dir", tmp_path)
    client = TestClient(app)
    assert client.get("/v1/health").json()["provider"] == "local-preview"

    content = b"unchanged reference"
    digest = hashlib.sha256(content).hexdigest()
    assert client.post("/v1/assets/check", json={"hashes": [digest]}).json() == {"missing": [digest]}
    assert client.put(f"/v1/assets/{digest}", content=content).status_code == 201
    assert client.post("/v1/assets/check", json={"hashes": [digest]}).json() == {"missing": []}


def test_colab_provider_fails_closed_until_a100_runtime_is_ready():
    client = TestClient(app)
    project = Project()
    response = client.post("/v1/jobs", json={"project": project.model_dump(mode="json"), "provider": "colab"})
    assert response.status_code == 503
    assert response.json()["detail"] == "A100 Colab backend is not ready"


def test_start_backend_reports_first_missing_remote_requirement():
    client = TestClient(app)
    response = client.post("/v1/backend/start?provider=colab")
    assert response.status_code == 503
    assert response.json()["detail"]


def test_reference_upload_is_normalized_stored_and_removable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "root", tmp_path)
    monkeypatch.setattr(store, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(store, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(store, "assets_dir", tmp_path / "assets")
    for directory in (store.projects_dir, store.jobs_dir, store.assets_dir):
        directory.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    project = Project()
    assert client.post("/v1/projects", json=project.model_dump(mode="json")).status_code == 201
    payload = io.BytesIO()
    Image.new("RGBA", (96, 128), (120, 60, 30, 190)).save(payload, "PNG")

    response = client.post(
        f"/v1/projects/{project.project_id}/references",
        data={"role": "face_front"},
        files={"file": ("portrait.png", payload.getvalue(), "image/png")},
    )

    assert response.status_code == 200
    document = response.json()
    reference = document["character"]["references"][0]
    assert reference["role"] == "face_front"
    stored = (
        tmp_path
        / "projects"
        / f"{project.project_id}.ffproject"
        / "references"
        / f"{reference['sha256']}.webp"
    )
    assert stored.is_file()
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == reference["sha256"]
    assert Image.open(stored).mode == "RGB"

    removed = client.delete(
        f"/v1/projects/{project.project_id}/references/{reference['reference_id']}"
    )
    assert removed.status_code == 200
    assert removed.json()["character"]["references"] == []
