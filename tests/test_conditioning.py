import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image

from services.local_controller.formframe.conditioning import (
    build_comparison_matrix_manifest,
    export_job,
    sha256,
    verify_bundle,
)
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
    validation = manifest["output"]["local_validation"]
    assert validation["path"] == "conditioning-contact-sheet.png"
    assert validation["passes"] == ["rgb", "depth", "pose"]
    assert validation["evidence_scope"] == "local-conditioning-export-only"
    contact_sheet = tmp_path / "jobs" / job.job_id / validation["path"]
    assert validation["sha256"] == sha256(contact_sheet)
    assert Image.open(contact_sheet).size == (768 * 3, 1024 + max(24, 1024 // 28))
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


def test_authoritative_normals_are_recorded_in_contact_sheet_and_bundle(tmp_path: Path):
    class NormalGeometry(ProceduralGuideGeometry):
        def conditioning_passes(self, project: Project):
            source = tmp_path / "normal-source"
            source.mkdir()
            rgb = source / "rgb.webp"
            depth = source / "depth.png"
            normal = source / "normal.png"
            Image.new("RGB", (512, 512), (21, 87, 143)).save(rgb, "WEBP", quality=92)
            Image.new("L", (512, 512), 177).save(depth, "PNG")
            Image.new("RGB", (512, 512), (127, 127, 255)).save(normal, "PNG")
            return {"rgb": rgb, "depth": depth, "normal": normal}

    project = Project()
    project.render.width = 512
    project.render.height = 512
    job = RenderJob(project_id=project.project_id)

    bundle, manifest, _, _ = export_job(project, job, tmp_path, geometry=NormalGeometry())

    assert manifest["assets"]["normal"]["path"] == "normal.png"
    assert manifest["output"]["local_validation"]["passes"] == ["rgb", "depth", "pose", "normal"]
    assert set(verify_bundle(bundle)) == {"depth.png", "manifest.json", "normal.png", "pose.png", "rgb.webp"}
    assert Image.open(tmp_path / "jobs" / job.job_id / "conditioning-contact-sheet.png").size == (2048, 536)


def test_comparison_matrix_scaffold_requires_live_a100_evidence(tmp_path: Path):
    project = Project()
    job = RenderJob(project_id=project.project_id)
    _, manifest, _, _ = export_job(project, job, tmp_path)

    matrix = build_comparison_matrix_manifest(manifest)

    assert [entry["variant"] for entry in matrix["variants"]] == ["A", "B", "C", "D", "E", "F"]
    assert {entry["status"] for entry in matrix["variants"]} == {"pending-live-a100"}
    assert "not live render evidence" in matrix["evidence_boundary"]
    assert matrix["required_live_evidence"] == [
        "a100_result_manifest",
        "result_png_sha256",
        "preview_webp_sha256",
        "runtime_gpu_probe",
        "workflow_hash",
        "model_manifest_sha256",
    ]


def test_comparison_matrix_cli_writes_pending_manifest(tmp_path: Path):
    project = Project()
    job = RenderJob(project_id=project.project_id)
    _, manifest, _, _ = export_job(project, job, tmp_path)
    manifest_path = tmp_path / "jobs" / job.job_id / "manifest.json"
    output_path = tmp_path / "matrix.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/create-comparison-matrix.py",
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == str(output_path)
    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert document["source_job_id"] == manifest["job_id"]
    assert len(document["variants"]) == 6
    assert document["variants"][0]["a100_result_manifest"] is None


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


def test_hair_and_garment_proxy_selections_change_local_conditioning(tmp_path: Path):
    fitted = Project()
    fitted_job = RenderJob(project_id=fitted.project_id)
    export_job(fitted, fitted_job, tmp_path)
    fitted_rgb = tmp_path / "jobs" / fitted_job.job_id / "rgb.webp"

    layered = Project()
    layered.character.appearance.hair_style = "Soft bob"
    layered.character.appearance.hair_proxy = "hair_proxy_soft_bob"
    layered.character.appearance.outfit = "Field jacket"
    layered.character.appearance.garment_proxy = "garment_proxy_field_jacket"
    layered_job = RenderJob(project_id=layered.project_id)
    export_job(layered, layered_job, tmp_path)
    layered_rgb = tmp_path / "jobs" / layered_job.job_id / "rgb.webp"

    assert sha256(layered_rgb) != sha256(fitted_rgb)
