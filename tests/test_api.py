import asyncio
import hashlib
import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from services.local_controller.formframe.app import app, runtime, store
from services.local_controller.formframe.models import Project


def _safetensors_bytes(header_document=None, payload: bytes = b"\0\0\0\0") -> bytes:
    header = json.dumps(
        header_document
        or {
            "transformer.layer.lora_A.weight": {
                "dtype": "F32",
                "shape": [1],
                "data_offsets": [0, 4],
            }
        },
        separators=(",", ":"),
    ).encode("utf-8")
    header += b" " * ((8 - len(header) % 8) % 8)
    return len(header).to_bytes(8, "little") + header + payload


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


def test_trained_identity_lora_is_validated_stored_and_detached(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(store, "root", tmp_path)
    monkeypatch.setattr(store, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(store, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(store, "assets_dir", tmp_path / "assets")
    for directory in (store.projects_dir, store.jobs_dir, store.assets_dir):
        directory.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    project = Project()
    assert client.post("/v1/projects", json=project.model_dump(mode="json")).status_code == 201
    content = _safetensors_bytes()
    digest = hashlib.sha256(content).hexdigest()

    invalid = client.post(
        f"/v1/projects/{project.project_id}/identity-lora",
        files={"file": ("identity.txt", b"not-a-lora", "application/octet-stream")},
    )
    assert invalid.status_code == 422

    response = client.post(
        f"/v1/projects/{project.project_id}/identity-lora",
        data={"trigger_token": "ff_mara", "strength": "0.85"},
        files={
            "file": (
                "mara.safetensors",
                content,
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    lora = response.json()["character"]["identity_lora"]
    assert lora == {
        "sha256": digest,
        "filename": "mara.safetensors",
        "bytes": len(content),
        "trigger_token": "ff_mara",
        "strength": 0.85,
    }
    assert (tmp_path / "assets" / digest).read_bytes() == content

    removed = client.delete(f"/v1/projects/{project.project_id}/identity-lora")
    assert removed.status_code == 200
    assert removed.json()["character"]["identity_lora"] is None
    assert (tmp_path / "assets" / digest).is_file()


def test_identity_lora_rejects_invalid_dtype_shape_and_overlapping_offsets(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(store, "root", tmp_path)
    monkeypatch.setattr(store, "projects_dir", tmp_path / "projects")
    monkeypatch.setattr(store, "jobs_dir", tmp_path / "jobs")
    monkeypatch.setattr(store, "assets_dir", tmp_path / "assets")
    for directory in (store.projects_dir, store.jobs_dir, store.assets_dir):
        directory.mkdir(parents=True, exist_ok=True)
    client = TestClient(app)
    project = Project()
    assert client.post("/v1/projects", json=project.model_dump(mode="json")).status_code == 201

    invalid_documents = [
        {
            "tensor": {
                "dtype": "NOT_A_DTYPE",
                "shape": [1],
                "data_offsets": [0, 4],
            }
        },
        {
            "tensor": {
                "dtype": "F32",
                "shape": [2],
                "data_offsets": [0, 4],
            }
        },
        {
            "tensor_a": {
                "dtype": "F32",
                "shape": [1],
                "data_offsets": [0, 4],
            },
            "tensor_b": {
                "dtype": "F32",
                "shape": [1],
                "data_offsets": [0, 4],
            },
        },
    ]
    for index, document in enumerate(invalid_documents):
        response = client.post(
            f"/v1/projects/{project.project_id}/identity-lora",
            files={
                "file": (
                    f"invalid-{index}.safetensors",
                    _safetensors_bytes(document),
                    "application/octet-stream",
                )
            },
        )
        assert response.status_code == 422
