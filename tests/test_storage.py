import sqlite3
from pathlib import Path

from services.local_controller.formframe.models import Project, RenderJob
from services.local_controller.formframe.storage import ProjectStore


def test_project_directory_round_trip(tmp_path: Path):
    store = ProjectStore(tmp_path)
    project = Project(name="Test portrait")
    store.save_project(project)

    project_dir = tmp_path / "projects" / f"{project.project_id}.ffproject"
    assert {
        "project.json",
        "character.json",
        "scene.json",
        "history.sqlite",
        "references",
        "proxies",
        "thumbnails",
        "renders",
    } <= {path.name for path in project_dir.iterdir()}
    assert store.get_project(project.project_id).model_dump() == project.model_dump()


def test_job_history_is_persisted_in_sqlite(tmp_path: Path):
    store = ProjectStore(tmp_path)
    project = store.save_project(Project())
    job = RenderJob(project_id=project.project_id, status="rendering", progress=76)
    store.save_job(job)

    history = tmp_path / "projects" / f"{project.project_id}.ffproject" / "history.sqlite"
    with sqlite3.connect(history) as connection:
        row = connection.execute("SELECT job_id, status, progress FROM render_jobs").fetchone()
    assert row == (job.job_id, "rendering", 76)
    assert store.list_jobs()[0].job_id == job.job_id
