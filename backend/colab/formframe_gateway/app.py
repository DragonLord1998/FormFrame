from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, Response

from .auth import AccessVerifier
from .bundle import sha256_path, validate_bundle
from .comfy import ComfyClient
from .models import AssetCheck, JobSubmission, RemoteJob, utc_now
from .settings import GatewaySettings

settings = GatewaySettings.from_environment()
settings.validate()
for directory in (settings.inbox, settings.work, settings.outbox):
    directory.mkdir(parents=True, exist_ok=True)

auth = AccessVerifier(settings)
workflow_path = settings.root / "workflows" / "controlled-character-v1.api.json"
comfy = ComfyClient(
    settings.comfy_url,
    workflow_path,
    settings.root / "ComfyUI" / "models" / "loras",
)
jobs: dict[str, RemoteJob] = {}
gpu_lock: Optional[asyncio.Lock] = None
submit_lock: Optional[asyncio.Lock] = None
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

app = FastAPI(
    title="FormFrame Private Gateway",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _job(job_id: str) -> RemoteJob:
    try:
        return jobs[job_id]
    except KeyError:
        raise HTTPException(status_code=404, detail="Remote job not found")


def _allowed_bundle(job_id: str, value: str) -> Path:
    expected = (settings.inbox / f"{job_id}.ffjob").resolve()
    requested = Path(value).resolve()
    if requested != expected:
        raise HTTPException(status_code=422, detail="Remote bundle path is not allowed")
    return requested


def _queue_size() -> int:
    return sum(1 for job in jobs.values() if job.status not in TERMINAL_STATUSES)


def _gpu_lock() -> asyncio.Lock:
    global gpu_lock
    if gpu_lock is None:
        gpu_lock = asyncio.Lock()
    return gpu_lock


def _submit_lock() -> asyncio.Lock:
    global submit_lock
    if submit_lock is None:
        submit_lock = asyncio.Lock()
    return submit_lock


def _require_sha256(value: str) -> str:
    digest = value.lower()
    if not SHA256_RE.fullmatch(digest):
        raise HTTPException(status_code=422, detail="Asset SHA-256 must be 64 lowercase hex characters")
    return digest


async def _read_limited_body(request: Request, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail="Asset payload exceeds the configured limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _asset_path(digest: str) -> Path:
    path = (settings.root / "assets" / digest).resolve()
    assets_root = (settings.root / "assets").resolve()
    if path.parent != assets_root:
        raise HTTPException(status_code=422, detail="Asset path is not allowed")
    return path


async def _authorize_websocket(websocket: WebSocket) -> bool:
    if settings.development_token:
        expected = f"Bearer {settings.development_token}"
        if hmac.compare_digest(websocket.headers.get("authorization", ""), expected):
            return True
        await websocket.close(code=4401)
        return False
    assertion = websocket.headers.get("cf-access-jwt-assertion", "")
    if not assertion:
        await websocket.close(code=4401)
        return False
    try:
        auth.verify(assertion)
    except HTTPException:
        await websocket.close(code=4401)
        return False
    return True


async def _send_job_events(websocket: WebSocket, job: RemoteJob) -> None:
    await websocket.accept()
    last_payload = ""
    while True:
        payload = job.model_dump(mode="json")
        serialized = job.model_dump_json()
        if serialized != last_payload:
            await websocket.send_json(payload)
            last_payload = serialized
        if job.status in TERMINAL_STATUSES:
            await websocket.close()
            return
        await asyncio.sleep(0.35)


@app.get("/v1/health")
async def health(_: dict = Depends(auth.dependency)):
    try:
        stats = await asyncio.to_thread(comfy.health)
        ready = True
    except Exception:
        stats = {}
        ready = False
    return {
        "status": "ready" if ready else "starting",
        "provider": "colab",
        "gpu": "A100",
        "workflow": "controlled-character-v1",
        "comfyui_private": True,
        "models_loaded": ready,
        "runtime_id": settings.runtime_id,
        "queue_size": _queue_size(),
        "stats": stats,
    }


@app.post("/v1/assets/check")
async def check_assets(payload: AssetCheck, _: dict = Depends(auth.dependency)):
    missing = []
    for value in payload.hashes:
        digest = _require_sha256(value)
        path = _asset_path(digest)
        if not path.is_file():
            missing.append(digest)
            continue
        observed = await asyncio.to_thread(sha256_path, path)
        if not hmac.compare_digest(observed, digest):
            missing.append(digest)
    return {"missing": missing}


@app.put("/v1/assets/{sha256}", status_code=201)
async def put_asset(sha256: str, request: Request, _: dict = Depends(auth.dependency)):
    digest = _require_sha256(sha256)
    content = await _read_limited_body(request, settings.max_asset_bytes)
    observed = hashlib.sha256(content).hexdigest()
    if not hmac.compare_digest(observed, digest):
        raise HTTPException(status_code=422, detail="Asset SHA-256 does not match payload")
    path = _asset_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    await asyncio.to_thread(tmp_path.write_bytes, content)
    tmp_path.replace(path)
    return {"sha256": digest, "bytes": len(content)}


@app.put("/v1/benchmark")
async def benchmark(request: Request, _: dict = Depends(auth.dependency)):
    content = await request.body()
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Benchmark payload exceeds 1 MB")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Content-SHA256": hashlib.sha256(content).hexdigest(),
        },
    )


@app.post("/v1/jobs", status_code=202)
async def submit(payload: JobSubmission, _: dict = Depends(auth.dependency)):
    async with _submit_lock():
        if payload.job_id in jobs:
            return jobs[payload.job_id]
        if _queue_size() >= settings.max_queue_size:
            raise HTTPException(status_code=429, detail="Gateway render queue is full")
        bundle = _allowed_bundle(payload.job_id, payload.remote_bundle)
        job = RemoteJob(
            job_id=payload.job_id,
            status="queued",
            stage="Queued",
            remote_bundle=str(bundle),
        )
        jobs[payload.job_id] = job
        asyncio.create_task(_run(payload.job_id, bundle))
    return job


async def _run(job_id: str, bundle: Path) -> None:
    job = jobs[job_id]
    try:
        async with _gpu_lock():
            if job.status == "cancelled":
                return
            job.status = "validating"
            job.progress = 10
            job.stage = "Validating immutable bundle"
            job.updated_at = utc_now()
            await asyncio.to_thread(
                validate_bundle,
                bundle,
                job_id,
                workflow_path,
                settings.root / "assets",
            )
            if job.status == "cancelled":
                return
            job.status = "rendering"
            job.progress = 25
            job.stage = "Submitting controlled-character-v1"
            job.updated_at = utc_now()
            prompt_id = await asyncio.to_thread(comfy.submit, bundle)
            job.prompt_id = prompt_id
            if job.status == "cancelled":
                await asyncio.to_thread(comfy.interrupt)
                return
            job.progress = 40
            job.stage = "Z-Image Turbo rendering"
            job.updated_at = utc_now()
            await asyncio.to_thread(comfy.wait, prompt_id)
            if job.status == "cancelled":
                return
            output_dir = settings.outbox / job_id
            result = output_dir / "result.png"
            preview = output_dir / "preview.webp"
            result_manifest = output_dir / "result.json"
            if not result.is_file() or not preview.is_file() or not result_manifest.is_file():
                raise RuntimeError("FormFrameResultSaver did not create the required outputs")
            job.status = "completed"
            job.progress = 100
            job.stage = "Complete"
            job.result_path = str(result)
            job.preview_path = str(preview)
            job.result_manifest_path = str(result_manifest)
            job.updated_at = utc_now()
    except Exception as exc:
        if job.status == "cancelled":
            return
        job.status = "failed"
        job.stage = "Remote render failed"
        job.error = str(exc)
        job.updated_at = utc_now()


@app.get("/v1/jobs/{job_id}")
async def get_job(job_id: str, _: dict = Depends(auth.dependency)):
    return _job(job_id)


@app.websocket("/v1/events")
async def events_legacy(websocket: WebSocket):
    if not await _authorize_websocket(websocket):
        return
    job_id = websocket.query_params.get("job_id", "")
    if not job_id:
        await websocket.close(code=4404)
        return
    try:
        job = _job(job_id)
    except HTTPException:
        await websocket.close(code=4404)
        return
    await _send_job_events(websocket, job)


@app.websocket("/v1/events/{job_id}")
async def events(websocket: WebSocket, job_id: str):
    if not await _authorize_websocket(websocket):
        return
    try:
        job = _job(job_id)
    except HTTPException:
        await websocket.close(code=4404)
        return
    await _send_job_events(websocket, job)


@app.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, _: dict = Depends(auth.dependency)):
    job = _job(job_id)
    if job.status not in TERMINAL_STATUSES:
        job.cancel_requested = True
        job.status = "cancelled"
        job.stage = "Cancelled"
        job.updated_at = utc_now()
        if job.prompt_id:
            await asyncio.to_thread(comfy.interrupt)
    return job


@app.get("/v1/jobs/{job_id}/preview")
async def preview(job_id: str, _: dict = Depends(auth.dependency)):
    job = _job(job_id)
    if job.status != "completed" or not job.preview_path:
        raise HTTPException(status_code=409, detail="Preview is not ready")
    return FileResponse(
        job.preview_path,
        media_type="image/webp",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/v1/jobs/{job_id}/result")
async def result(job_id: str, _: dict = Depends(auth.dependency)):
    job = _job(job_id)
    if job.status != "completed" or not job.result_path:
        raise HTTPException(status_code=409, detail="Result is not ready")
    return FileResponse(
        job.result_path,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
