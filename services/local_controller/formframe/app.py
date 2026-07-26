from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from .models import AssetCheck, AssetCheckResult, Project, ReferenceImage, RenderRequest
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


@app.get("/v1/health")
async def health():
    return runtime.snapshot


@app.post("/v1/backend/start", status_code=202)
async def start_backend(provider: Literal["colab", "local-preview"] = "colab"):
    snapshot = await runtime.start(provider)
    if provider == "colab" and snapshot.status == "offline" and snapshot.readiness_errors:
        raise HTTPException(503, snapshot.readiness_errors[0])
    return snapshot


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
