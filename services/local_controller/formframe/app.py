from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from bridge.colab_cli import ColabCliError

from .models import (
    AssetCheck,
    AssetCheckResult,
    IdentityLora,
    Project,
    ReferenceImage,
    RenderRequest,
)
from .config import FormFrameSettings
from .runtime import RuntimeManager
from .storage import ProjectStore


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = Path(os.environ.get("FORMFRAME_DATA_DIR", REPO_ROOT / "data")).resolve()
store = ProjectStore(DATA_ROOT)
settings = FormFrameSettings.from_environment()
runtime = RuntimeManager(store, settings=settings, repo_root=REPO_ROOT)

app = FastAPI(
    title="FormFrame Local Controller",
    version="0.1.0",
    description="Local-first character staging, conditioning export, and render orchestration.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:7860", "http://localhost:7860", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_IDENTITY_LORA_BYTES = 2 * 1024 * 1024 * 1024
MAX_SAFETENSORS_HEADER_BYTES = 16 * 1024 * 1024
SAFETENSORS_DTYPE_BYTES = {
    "BOOL": 1,
    "U8": 1,
    "I8": 1,
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "F8_E4M3FN": 1,
    "F8_E5M2FN": 1,
    "I16": 2,
    "U16": 2,
    "F16": 2,
    "BF16": 2,
    "I32": 4,
    "U32": 4,
    "F32": 4,
    "I64": 8,
    "U64": 8,
    "F64": 8,
}


def _validate_safetensors(path: Path) -> None:
    size = path.stat().st_size
    if size < 10:
        raise ValueError("Identity LoRA is not a valid safetensors file")
    with path.open("rb") as stream:
        header_size = int.from_bytes(stream.read(8), "little", signed=False)
        if header_size < 2 or header_size > MAX_SAFETENSORS_HEADER_BYTES:
            raise ValueError("Identity LoRA has an invalid safetensors header")
        if 8 + header_size > size:
            raise ValueError("Identity LoRA safetensors header is truncated")
        try:
            header = json.loads(stream.read(header_size))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Identity LoRA safetensors header is invalid") from exc
    if not isinstance(header, dict):
        raise ValueError("Identity LoRA safetensors header must be an object")
    metadata = header.get("__metadata__")
    if metadata is not None and (
        not isinstance(metadata, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in metadata.items())
    ):
        raise ValueError("Identity LoRA safetensors metadata is invalid")
    payload_size = size - 8 - header_size
    intervals: list[tuple[int, int]] = []
    for name, descriptor in header.items():
        if name == "__metadata__":
            continue
        if not isinstance(name, str) or not isinstance(descriptor, dict):
            raise ValueError("Identity LoRA safetensors tensor metadata is invalid")
        dtype = descriptor.get("dtype")
        shape = descriptor.get("shape")
        offsets = descriptor.get("data_offsets")
        if (
            dtype not in SAFETENSORS_DTYPE_BYTES
            or not isinstance(shape, list)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in shape
            )
            or not isinstance(offsets, list)
            or len(offsets) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in offsets)
        ):
            raise ValueError("Identity LoRA safetensors tensor metadata is invalid")
        start, end = offsets
        if start < 0 or end < start or end > payload_size:
            raise ValueError("Identity LoRA safetensors data offsets are invalid")
        expected_bytes = math.prod(shape) * SAFETENSORS_DTYPE_BYTES[dtype]
        if end - start != expected_bytes:
            raise ValueError("Identity LoRA safetensors tensor size does not match its shape")
        intervals.append((start, end))
    if not intervals:
        raise ValueError("Identity LoRA safetensors contains no tensors")
    cursor = 0
    for start, end in sorted(intervals):
        if start != cursor:
            raise ValueError("Identity LoRA safetensors tensor data must be contiguous and non-overlapping")
        cursor = end
    if cursor != payload_size:
        raise ValueError("Identity LoRA safetensors payload is incomplete")


@app.get("/v1/health")
async def health():
    return runtime.snapshot


@app.post("/v1/backend/start", status_code=202)
async def start_backend(provider: Literal["colab", "local-preview"] = "colab"):
    snapshot = await runtime.start(provider)
    if provider == "colab" and snapshot.status == "offline" and snapshot.readiness_errors:
        raise HTTPException(503, snapshot.readiness_errors[0])
    return snapshot


@app.post("/v1/backend/stop")
async def stop_backend():
    try:
        return await runtime.stop()
    except ColabCliError as exc:
        raise HTTPException(503, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/v1/projects")
async def list_projects():
    return store.list_projects()


@app.post("/v1/projects", status_code=201)
async def create_project(project: Project):
    return store.save_project(project)


@app.get("/v1/projects/{project_id}")
async def get_project(project_id: str):
    try:
        return store.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")


@app.put("/v1/projects/{project_id}")
async def update_project(project_id: str, project: Project):
    if project.project_id != project_id:
        raise HTTPException(409, "Project ID does not match route")
    return store.save_project(project)


@app.post("/v1/projects/{project_id}/references")
async def upload_reference(
    project_id: str,
    role: Literal["face_front", "face_left", "face_right", "outfit"] = Form(...),
    file: UploadFile = File(...),
):
    try:
        project = store.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "Reference image exceeds 20 MB")
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
        image = image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(422, "Reference must be a valid image")
    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
    encoded = io.BytesIO()
    image.save(encoded, "WEBP", quality=94, method=4)
    normalized = encoded.getvalue()
    digest = hashlib.sha256(normalized).hexdigest()
    store.save_reference(project_id, digest, normalized)
    reference = ReferenceImage(
        role=role,
        sha256=digest,
        filename=Path(file.filename or f"{role}.webp").name,
        width=image.width,
        height=image.height,
    )
    project.character.references = [
        existing for existing in project.character.references if existing.role != role
    ] + [reference]
    return store.save_project(project)


@app.delete("/v1/projects/{project_id}/references/{reference_id}")
async def remove_reference(project_id: str, reference_id: str):
    try:
        project = store.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    references = [
        reference
        for reference in project.character.references
        if reference.reference_id != reference_id
    ]
    if len(references) == len(project.character.references):
        raise HTTPException(404, "Reference not found")
    project.character.references = references
    return store.save_project(project)


@app.post("/v1/projects/{project_id}/identity-lora")
async def attach_identity_lora(
    project_id: str,
    file: UploadFile = File(...),
    trigger_token: str = Form("ff_character"),
    strength: float = Form(1.0),
):
    try:
        project = store.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    filename = Path(file.filename or "identity.safetensors").name
    if not filename.lower().endswith(".safetensors"):
        raise HTTPException(422, "Identity LoRA must use the .safetensors format")
    store.assets_dir.mkdir(parents=True, exist_ok=True)
    temporary = store.assets_dir / f".identity-lora-{uuid4().hex}.part"
    digest = hashlib.sha256()
    total = 0
    try:
        with temporary.open("wb") as output:
            while True:
                chunk = await file.read(8 * 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IDENTITY_LORA_BYTES:
                    raise HTTPException(413, "Identity LoRA exceeds 2 GB")
                digest.update(chunk)
                output.write(chunk)
        try:
            _validate_safetensors(temporary)
            metadata = IdentityLora(
                sha256=digest.hexdigest(),
                filename=filename,
                bytes=total,
                trigger_token=trigger_token.strip(),
                strength=strength,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        store.save_asset_file(metadata.sha256, temporary)
    finally:
        temporary.unlink(missing_ok=True)
    project.character.identity_lora = metadata
    return store.save_project(project)


@app.delete("/v1/projects/{project_id}/identity-lora")
async def remove_identity_lora(project_id: str):
    try:
        project = store.get_project(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    project.character.identity_lora = None
    return store.save_project(project)


@app.delete("/v1/projects/{project_id}", status_code=204)
async def delete_project(project_id: str):
    try:
        store.delete_project(project_id)
    except KeyError:
        raise HTTPException(404, "Project not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/v1/assets/check", response_model=AssetCheckResult)
async def check_assets(payload: AssetCheck):
    return AssetCheckResult(missing=[digest for digest in payload.hashes if not store.asset_path(digest).exists()])


@app.post("/v1/geometry/evaluate")
async def evaluate_geometry(project: Project):
    try:
        return await runtime.evaluate_geometry(project)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.get("/v1/geometry/{digest}/character.glb")
async def geometry_mesh(digest: str):
    if len(digest) != 20 or any(value not in "0123456789abcdef" for value in digest):
        raise HTTPException(422, "Geometry cache ID is invalid")
    path = (DATA_ROOT / "geometry" / digest / "character.glb").resolve()
    if (DATA_ROOT / "geometry").resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Geometry mesh not found")
    return FileResponse(path, media_type="model/gltf-binary", headers={"Cache-Control": "no-store"})


@app.put("/v1/assets/{digest}", status_code=201)
async def put_asset(digest: str, request: Request):
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise HTTPException(422, "Asset ID must be a lowercase SHA-256 digest")
    content = await request.body()
    if len(content) > 32 * 1024 * 1024:
        raise HTTPException(413, "Asset exceeds the 32 MB gateway limit")
    import hashlib
    if hashlib.sha256(content).hexdigest() != digest:
        raise HTTPException(422, "Asset hash mismatch")
    store.asset_path(digest).write_bytes(content)
    return {"sha256": digest, "bytes": len(content)}


@app.post("/v1/jobs", status_code=202)
async def create_job(payload: RenderRequest):
    if payload.provider == "colab" and (
        runtime.snapshot.status not in {"ready", "rendering"}
        or runtime.snapshot.provider != "colab"
    ):
        raise HTTPException(503, "A100 Colab backend is not ready")
    store.save_project(payload.project)
    return await runtime.create_job(payload.project, payload.provider)


@app.get("/v1/jobs")
async def list_jobs():
    return sorted(runtime.jobs.values(), key=lambda item: item.created_at, reverse=True)


def _job_or_404(job_id: str):
    try:
        return runtime.jobs[job_id]
    except KeyError:
        raise HTTPException(404, "Job not found")


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str):
    return _job_or_404(job_id)


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    _job_or_404(job_id)
    return await runtime.cancel_job(job_id)


@app.get("/v1/jobs/{job_id}/preview")
async def job_preview(job_id: str):
    job = _job_or_404(job_id)
    path = DATA_ROOT / "jobs" / job_id / "preview.webp"
    if job.status != "completed" or not path.exists():
        raise HTTPException(409, "Preview is not ready")
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/result")
async def job_result(job_id: str):
    job = _job_or_404(job_id)
    path = DATA_ROOT / "jobs" / job_id / "result.png"
    if job.status != "completed" or not path.exists():
        raise HTTPException(409, "Result is not ready")
    return FileResponse(path, media_type="image/png", filename=f"{job_id}.png", headers={"Cache-Control": "no-store"})


@app.get("/v1/jobs/{job_id}/bundle")
async def job_bundle(job_id: str):
    job = _job_or_404(job_id)
    if not job.bundle_path:
        raise HTTPException(409, "Bundle is not ready")
    path = Path(job.bundle_path)
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.websocket("/v1/events")
async def events(websocket: WebSocket):
    await runtime.broker.connect(websocket)
    await websocket.send_json({"type": "runtime", "runtime": runtime.snapshot.model_dump(mode="json")})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        runtime.broker.disconnect(websocket)


@app.get("/")
async def root():
    return {
        "name": "FormFrame Local Controller",
        "docs": "/docs",
        "studio": "http://127.0.0.1:7860",
        "provider": runtime.snapshot.provider,
        "remote_configured": not bool(runtime.snapshot.readiness_errors),
    }
