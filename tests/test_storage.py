import json
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


def test_character_library_selections_are_persisted_in_character_split(tmp_path: Path):
    store = ProjectStore(tmp_path)
    project = Project(name="Library portrait")
    project.character.preset = "Noor / Editorial"
    project.character.appearance.hair_style = "Soft bob"
    project.character.appearance.hair_proxy = "hair_proxy_soft_bob"
    project.character.appearance.outfit = "Field jacket"
    project.character.appearance.garment_proxy = "garment_proxy_field_jacket"

    store.save_project(project)

    character_path = tmp_path / "projects" / f"{project.project_id}.ffproject" / "character.json"
    character = json.loads(character_path.read_text())
    assert character["preset"] == "Noor / Editorial"
    assert character["appearance"]["hair_proxy"] == "hair_proxy_soft_bob"
    assert character["appearance"]["garment_proxy"] == "garment_proxy_field_jacket"
    assert store.get_project(project.project_id).character.appearance.garment_proxy == "garment_proxy_field_jacket"


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
