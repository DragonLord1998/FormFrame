from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import List

from .models import Project, RenderJob, utc_now


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.projects_dir = root / "projects"
        self.jobs_dir = root / "jobs"
        self.assets_dir = root / "assets"
        for directory in (self.projects_dir, self.jobs_dir, self.assets_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def list_projects(self) -> List[Project]:
        projects = []
        for path in sorted(self.projects_dir.glob("*.ffproject/project.json")):
            projects.append(Project.model_validate_json(path.read_text()))
        return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def get_project(self, project_id: str) -> Project:
        path = self._project_dir(project_id) / "project.json"
        if not path.exists():
            raise KeyError(project_id)
        return Project.model_validate_json(path.read_text())

    def save_project(self, project: Project) -> Project:
        project.updated_at = utc_now()
        project_dir = self._project_dir(project.project_id)
        for directory_name in ("references", "proxies", "thumbnails", "renders"):
            (project_dir / directory_name).mkdir(parents=True, exist_ok=True)
        self._atomic_json(project_dir / "project.json", project.model_dump(mode="json"))
        self._atomic_json(project_dir / "character.json", project.character.model_dump(mode="json"))
        self._atomic_json(
            project_dir / "scene.json",
            {
                "schema_version": project.schema_version,
                "pose": project.pose.model_dump(mode="json"),
                "scene": project.scene.model_dump(mode="json"),
                "render": project.render.model_dump(mode="json"),
            },
        )
        self._initialize_history(project_dir / "history.sqlite")
        return project

    def delete_project(self, project_id: str) -> None:
        project_dir = self._project_dir(project_id)
        if not project_dir.exists():
            raise KeyError(project_id)
        # Project deletion is intentionally conservative: only known files created
        # by FormFrame are removed, and unknown user files prevent deletion.
        known_files = {
            project_dir / "project.json",
            project_dir / "character.json",
            project_dir / "scene.json",
            project_dir / "history.sqlite",
        }
        known_dirs = {project_dir / name for name in ("references", "proxies", "thumbnails", "renders")}
        unknown = [
            path
            for path in project_dir.rglob("*")
            if path.is_file() and path not in known_files and not any(parent in path.parents for parent in known_dirs)
        ]
        if unknown:
            raise ValueError("Project contains unknown files and was not deleted")
        for directory in known_dirs:
            for path in sorted(directory.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            if directory.exists():
                directory.rmdir()
        for path in known_files:
            if path.exists():
                path.unlink()
        project_dir.rmdir()

    def save_job(self, job: object) -> None:
        payload = job.model_dump(mode="json")  # type: ignore[attr-defined]
        self._atomic_json(self.jobs_dir / f"{payload['job_id']}.json", payload)
        history_path = self._project_dir(payload["project_id"]) / "history.sqlite"
        if history_path.exists():
            with sqlite3.connect(history_path) as connection:
                connection.execute(
                    """
                    INSERT INTO render_jobs(job_id, status, progress, stage, provider, workflow, bundle_path, error, created_at, updated_at)
                    VALUES(:job_id, :status, :progress, :stage, :provider, :workflow, :bundle_path, :error, :created_at, :updated_at)
                    ON CONFLICT(job_id) DO UPDATE SET
                        status=excluded.status,
                        progress=excluded.progress,
                        stage=excluded.stage,
                        bundle_path=excluded.bundle_path,
                        error=excluded.error,
                        updated_at=excluded.updated_at
                    """,
                    payload,
                )
                connection.commit()

    def list_jobs(self) -> List[RenderJob]:
        jobs = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            jobs.append(RenderJob.model_validate_json(path.read_text()))
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    def asset_path(self, digest: str) -> Path:
        return self.assets_dir / digest

    def save_asset_file(self, digest: str, source: Path) -> Path:
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("Asset digest must be a lowercase SHA-256 value")
        if not source.is_file():
            raise ValueError("Asset source is missing")
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        destination = self.asset_path(digest)
        if destination.is_file():
            observed = hashlib.sha256()
            with destination.open("rb") as stream:
                for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                    observed.update(chunk)
            if observed.hexdigest() == digest:
                source.unlink()
                return destination
            destination.unlink()
        source.replace(destination)
        return destination

    def save_reference(self, project_id: str, digest: str, content: bytes) -> Path:
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ValueError("Reference digest must be a lowercase SHA-256 value")
        project_dir = self._project_dir(project_id)
        if not (project_dir / "project.json").is_file():
            raise KeyError(project_id)
        references = project_dir / "references"
        references.mkdir(parents=True, exist_ok=True)
        destination = references / f"{digest}.webp"
        if not destination.is_file():
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
        return destination

    def reference_path(self, project_id: str, digest: str) -> Path:
        return self._project_dir(project_id) / "references" / f"{digest}.webp"

    def _project_dir(self, project_id: str) -> Path:
        return self.projects_dir / f"{project_id}.ffproject"

    @staticmethod
    def _initialize_history(path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS render_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    workflow TEXT NOT NULL,
                    bundle_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    @staticmethod
    def _atomic_json(path: Path, payload: object) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
        temporary.replace(path)
