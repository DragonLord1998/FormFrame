import hashlib
import json
import zipfile
from pathlib import Path

from PIL import Image

from services.local_controller.formframe.conditioning import export_job, sha256, verify_bundle
from services.local_controller.formframe.geometry import ProceduralGuideGeometry
from services.local_controller.formframe.models import Project, ReferenceImage, RenderJob


def test_conditioning_assets_share_dimensions_and_hashes(tmp_path: Path):
    project = Project()
    project.render.width = 768
    project.render.height = 1024
    job = RenderJob(project_id=project.project_id)

    bundle, manifest, _preview, _result = export_job(project, job, tmp_path)

    dimensions = {
        Image.open(tmp_path / "jobs" / job.job_id / filename).size
        for filename in ("rgb.webp", "depth.png", "pose.png")
    }
    assert dimensions == {(768, 1024)}
    assert manifest["controls"]["normal_strength"] == 0
    assert "normal" not in manifest["assets"]
    for asset in (value for value in manifest["assets"].values() if isinstance(value, dict)):
        assert asset["sha256"] == sha256(tmp_path / "jobs" / job.job_id / asset["path"])
    assert set(verify_bundle(bundle)) == {"depth.png", "manifest.json", "pose.png", "rgb.webp"}


def test_ffjob_uses_storage_mode_and_has_no_unsafe_paths(tmp_path: Path):
    project = Project()
    job = RenderJob(project_id=project.project_id)
    bundle, _manifest, _preview, _result = export_job(project, job, tmp_path)

    with zipfile.ZipFile(bundle) as archive:
        assert all(item.compress_type == zipfile.ZIP_STORED for item in archive.infolist())
        assert all(not item.filename.startswith("/") and ".." not in Path(item.filename).parts for item in archive.infolist())
        payload = json.loads(archive.read("manifest.json"))
        assert payload["workflow"] == "controlled-character-v1"
        assert payload["provider"] == "local-preview"


def test_real_geometry_conditioning_passes_are_used_without_redrawing(tmp_path: Path):
    class AuthoritativeGeometry(ProceduralGuideGeometry):
        provider_id = "gnm-v3-smplx"

        def conditioning_passes(self, project: Project):
            source = tmp_path / "authoritative"
            source.mkdir()
            rgb = source / "rgb.webp"
            depth = source / "depth.png"
            Image.new("RGB", (512, 512), (21, 87, 143)).save(rgb, "WEBP", quality=92)
            Image.new("L", (512, 512), 177).save(depth, "PNG")
            return {"rgb": rgb, "depth": depth}

    project = Project()
    project.render.width = 512
    project.render.height = 512
    job = RenderJob(project_id=project.project_id, provider="colab")
    _, manifest, _, _ = export_job(
        project,
        job,
        tmp_path,
        geometry=AuthoritativeGeometry(),
        create_local_result=False,
    )
    job_dir = tmp_path / "jobs" / job.job_id

    assert Image.open(job_dir / "rgb.webp").getpixel((12, 12))[2] > 100
    assert Image.open(job_dir / "depth.png").getpixel((12, 12)) == 177
    assert manifest["versions"]["geometry_provider"] == "gnm-v3-smplx"


def test_identity_training_references_are_hash_pinned_in_bundle(tmp_path: Path):
    project = Project()
    reference_dir = (
        tmp_path
        / "projects"
        / f"{project.project_id}.ffproject"
        / "references"
    )
    reference_dir.mkdir(parents=True)
    source = reference_dir / "pending.webp"
    Image.new("RGB", (48, 64), (87, 43, 21)).save(source, "WEBP", quality=94)
    digest = sha256(source)
    source.rename(reference_dir / f"{digest}.webp")
    project.character.references = [
        ReferenceImage(
            role="face_front",
            sha256=digest,
            filename="portrait.png",
            width=48,
            height=64,
        )
    ]
    job = RenderJob(project_id=project.project_id)

    bundle, manifest, _preview, _result = export_job(project, job, tmp_path)

    reference = manifest["assets"]["references"][0]
    assert reference["role"] == "face_front"
    assert reference["sha256"] == digest
    assert manifest["controls"]["identity_mode"] == "trained-lora-required"
    with zipfile.ZipFile(bundle) as archive:
        assert reference["path"] in archive.namelist()
        assert hashlib.sha256(archive.read(reference["path"])).hexdigest() == digest
