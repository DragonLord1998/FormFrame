from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, Literal, Set

from fastapi import WebSocket

from bridge.colab_cli import ColabCliError

from .conditioning import export_job
from .config import FormFrameSettings
from .geometry import GnmSmplxGeometry, ProceduralGuideGeometry
from .models import Project, RenderJob, RuntimeSnapshot, utc_now
from .remote import ColabRemoteRuntime, RemoteRuntimeError
from .storage import ProjectStore


class EventBroker:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def broadcast(self, payload: dict) -> None:
        stale = []
        for connection in self.connections:
            try:
                await connection.send_json(payload)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.connections.discard(connection)


class RuntimeManager:
    def __init__(
        self,
        store: ProjectStore,
        settings: FormFrameSettings | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or FormFrameSettings.from_environment()
        self.repo_root = repo_root or Path(__file__).resolve().parents[3]
        readiness_errors = self.settings.remote_readiness_errors()
        self.snapshot = RuntimeSnapshot(
            capabilities=["local-editing", "local-preview", "colab-a100", "cloudflare-control"],
            readiness_errors=readiness_errors,
            detail=(
                "Local editing is available. Configure the listed remote requirements to start A100 rendering."
                if readiness_errors
                else "Local editing is available. The A100 runtime is configured and can be started."
            ),
        )
        self.jobs: Dict[str, RenderJob] = {job.job_id: job for job in store.list_jobs()}
        self.projects: Dict[str, Project] = {}
        self.broker = EventBroker()
        self._start_task: asyncio.Task | None = None
        self._job_lock = asyncio.Lock()
        self._remote: ColabRemoteRuntime | None = None
        self._production_geometry: GnmSmplxGeometry | None = None

    async def start(self, provider: Literal["colab", "local-preview"] = "colab") -> RuntimeSnapshot:
        if self.snapshot.status in {"ready", "rendering"} and self.snapshot.provider == provider:
            return self.snapshot
        if self._start_task and not self._start_task.done():
            return self.snapshot
        self._start_task = asyncio.create_task(
            self._start_colab_sequence() if provider == "colab" else self._start_local_sequence()
        )
        return self.snapshot

    async def _start_local_sequence(self) -> None:
        stages = [
            ("provisioning", "Provisioning local preview", 12, "Preparing the render worker"),
            ("installing", "Checking backend", 30, "Validating pinned workflow"),
            ("restoring", "Restoring assets", 52, "Opening the content-addressed cache"),
            ("starting", "Starting render service", 70, "Binding the private gateway"),
            ("loading", "Loading guide workflow", 84, "Loading controlled-character-v1"),
            ("warming", "Warming workflow", 94, "Running a deterministic health check"),
        ]
        for status, label, progress, detail in stages:
            self.snapshot = RuntimeSnapshot(
                status=status,
                label=label,
                progress=progress,
                workflow="controlled-character-v1",
                provider="local-preview",
                capabilities=self.snapshot.capabilities,
                readiness_errors=self.settings.remote_readiness_errors(),
                detail=detail,
            )
            await self.broker.broadcast({"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")})
            await asyncio.sleep(0.22)
        self.snapshot = RuntimeSnapshot(
            status="ready",
            label="Ready",
            progress=100,
            gpu="Local preview",
            workflow="controlled-character-v1",
            models_loaded=True,
            runtime_id="local-preview-01",
            provider="local-preview",
            capabilities=self.snapshot.capabilities,
            readiness_errors=self.settings.remote_readiness_errors(),
            detail="Deterministic conditioning preview is ready. Colab adapter is not configured.",
        )
        await self.broker.broadcast({"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")})

    async def _start_colab_sequence(self) -> None:
        errors = self.settings.remote_readiness_errors()
        if errors:
            self.snapshot = RuntimeSnapshot(
                status="offline",
                label="Configuration required",
                provider="colab",
                capabilities=self.snapshot.capabilities,
                readiness_errors=errors,
                detail=errors[0],
            )
            await self.broker.broadcast(
                {"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")}
            )
            return
        loop = asyncio.get_running_loop()
        try:
            self._remote = ColabRemoteRuntime(self.settings, self.repo_root)
            self._production_geometry = GnmSmplxGeometry(
                self.settings,
                self.store.root / "geometry",
                self.settings.geometry_python,
            )

            def progress(status: str, label: str, percent: int, detail: str) -> None:
                self.snapshot = RuntimeSnapshot(
                    status=status,
                    label=label,
                    progress=percent,
                    workflow="controlled-character-v1",
                    provider="colab",
                    gpu="A100",
                    capabilities=self.snapshot.capabilities,
                    readiness_errors=[],
                    detail=detail,
                )
                asyncio.run_coroutine_threadsafe(
                    self.broker.broadcast(
                        {"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")}
                    ),
                    loop,
                )

            health = await asyncio.to_thread(self._remote.start, progress)
            self.snapshot = RuntimeSnapshot(
                status="ready",
                label="A100 ready",
                progress=100,
                gpu=str(health.get("gpu", "A100")),
                workflow="controlled-character-v1",
                models_loaded=bool(health.get("models_loaded", True)),
                runtime_id=self.settings.colab_session,
                provider="colab",
                capabilities=self.snapshot.capabilities,
                readiness_errors=[],
                detail="Private ComfyUI is ready through the authenticated Cloudflare gateway.",
            )
        except Exception as exc:
            self._remote = None
            self.snapshot = RuntimeSnapshot(
                status="offline",
                label="Backend failed",
                provider="colab",
                capabilities=self.snapshot.capabilities,
                readiness_errors=[str(exc)],
                detail=str(exc),
            )
        await self.broker.broadcast(
            {"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")}
        )

    async def create_job(self, project: Project, provider: str) -> RenderJob:
        job = RenderJob(project_id=project.project_id, provider=provider)
        self.jobs[job.job_id] = job
        self.projects[job.job_id] = project
        self.store.save_job(job)
        asyncio.create_task(self._run_job(job.job_id))
        return job

    async def evaluate_geometry(self, project: Project) -> dict[str, object]:
        if self._production_geometry is None:
            self._production_geometry = GnmSmplxGeometry(
                self.settings,
                self.store.root / "geometry",
                self.settings.geometry_python,
            )
        result = await asyncio.to_thread(self._production_geometry.evaluate, project)
        mesh_path = Path(str(result["mesh_path"])).resolve()
        geometry_root = (self.store.root / "geometry").resolve()
        if geometry_root not in mesh_path.parents:
            raise RuntimeError("Geometry worker returned a path outside the cache")
        digest = mesh_path.parent.name
        return {
            **result,
            "mesh_url": f"/v1/geometry/{digest}/character.glb",
        }

    async def cancel_job(self, job_id: str) -> RenderJob:
        job = self.jobs[job_id]
        if job.status not in {"completed", "failed"}:
            job.status = "cancelled"
            job.stage = "Cancelled"
            job.updated_at = utc_now()
            self.store.save_job(job)
            await self._broadcast_job(job)
            if job.provider == "colab" and self._remote:
                await asyncio.to_thread(self._remote.cancel, job_id)
        return job

    async def _run_job(self, job_id: str) -> None:
        async with self._job_lock:
            job = self.jobs[job_id]
            project = self.projects[job_id]
            stages = [
                ("freezing", 12, "Freezing scene", 0.18),
                ("exporting", 34, "Rendering conditioning passes", 0.22),
                ("packaging", 58, "Hashing and packaging assets", 0.18),
            ]
            try:
                self.snapshot.status = "rendering"
                self.snapshot.label = "Rendering"
                self.snapshot.queue_size = max(0, len([j for j in self.jobs.values() if j.status == "queued"]) - 1)
                for status, progress, label, delay in stages:
                    if job.status == "cancelled":
                        return
                    job.status = status
                    job.progress = progress
                    job.stage = label
                    job.updated_at = utc_now()
                    self.store.save_job(job)
                    await self._broadcast_job(job)
                    await asyncio.sleep(delay)
                geometry = (
                    self._production_geometry
                    if job.provider == "colab"
                    else ProceduralGuideGeometry()
                )
                if job.provider == "colab" and (not self._remote or not geometry):
                    raise RemoteRuntimeError("A100 runtime and GNM/SMPL-X geometry are not ready")
                bundle, manifest, _preview, _result = await asyncio.to_thread(
                    export_job,
                    project,
                    job,
                    self.store.root,
                    geometry,
                    job.provider != "colab",
                )
                if job.status == "cancelled":
                    return
                if job.provider == "colab":
                    job.status = "rendering"
                    job.progress = 61
                    job.stage = "Uploading to A100"
                    self.store.save_job(job)
                    await self._broadcast_job(job)
                    loop = asyncio.get_running_loop()

                    def remote_progress(percent: int, stage: str) -> None:
                        job.progress = percent
                        job.stage = stage
                        job.updated_at = utc_now()
                        self.store.save_job(job)
                        asyncio.run_coroutine_threadsafe(self._broadcast_job(job), loop)

                    assert self._remote is not None
                    remote_result = await self._render_remote_with_recovery(
                        job,
                        bundle,
                        self.store.root / "jobs" / job.job_id,
                        remote_progress,
                    )
                    manifest["remote_result"] = remote_result.result_manifest
                    manifest["transport"] = {
                        "bulk": "colab-cli",
                        "control": "cloudflare",
                        "preview": "cloudflare",
                        "final_png": "colab-cli",
                        "cli_fallback_used": remote_result.used_cli_fallback,
                        "session_metrics": self._remote.transfer_metrics,
                        "transfer_plan": remote_result.transfer_plan,
                    }
                job.status = "completed"
                job.progress = 100
                job.stage = "A100 render complete" if job.provider == "colab" else "Preview complete"
                job.bundle_path = str(bundle)
                job.preview_url = f"/v1/jobs/{job.job_id}/preview"
                job.result_url = f"/v1/jobs/{job.job_id}/result"
                job.manifest = manifest
                job.updated_at = utc_now()
                self.store.save_job(job)
                await self._broadcast_job(job)
            except Exception as exc:
                job.status = "failed"
                job.stage = "Render failed"
                job.error = str(exc)
                job.updated_at = utc_now()
                self.store.save_job(job)
                await self._broadcast_job(job)
            finally:
                if self.snapshot.status != "offline":
                    self.snapshot.status = "ready"
                    self.snapshot.label = "A100 ready" if self.snapshot.provider == "colab" else "Ready"
                self.snapshot.queue_size = len([j for j in self.jobs.values() if j.status == "queued"])
                await self.broker.broadcast({"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")})

    async def _render_remote_with_recovery(
        self,
        job: RenderJob,
        bundle: Path,
        local_job_dir: Path,
        remote_progress,
    ):
        assert self._remote is not None
        try:
            return await asyncio.to_thread(
                self._remote.render,
                job.job_id,
                bundle,
                local_job_dir,
                remote_progress,
            )
        except ColabCliError:
            if job.status == "cancelled":
                raise
        self.snapshot.status = "recovering"
        self.snapshot.label = "Recovering A100"
        self.snapshot.progress = 68
        self.snapshot.detail = "The Colab session disappeared; reconnecting and restoring the pinned runtime once."
        await self.broker.broadcast(
            {"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")}
        )
        job.stage = "Recovering A100 runtime"
        job.updated_at = utc_now()
        self.store.save_job(job)
        await self._broadcast_job(job)
        loop = asyncio.get_running_loop()

        def recovery_progress(status: str, label: str, percent: int, detail: str) -> None:
            self.snapshot.status = "recovering"
            self.snapshot.label = label
            self.snapshot.progress = percent
            self.snapshot.detail = detail
            asyncio.run_coroutine_threadsafe(
                self.broker.broadcast(
                    {"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")}
                ),
                loop,
            )

        await asyncio.to_thread(self._remote.start, recovery_progress)
        if job.status == "cancelled":
            raise ColabCliError("Render was cancelled during runtime recovery")
        self.snapshot.status = "rendering"
        self.snapshot.label = "A100 restored"
        self.snapshot.progress = 72
        self.snapshot.detail = "Pinned runtime restored; retrying the immutable job bundle once."
        await self.broker.broadcast(
            {"type": "runtime", "runtime": self.snapshot.model_dump(mode="json")}
        )
        return await asyncio.to_thread(
            self._remote.render,
            job.job_id,
            bundle,
            local_job_dir,
            remote_progress,
        )

    async def _broadcast_job(self, job: RenderJob) -> None:
        await self.broker.broadcast({"type": "job", "job": job.model_dump(mode="json")})
