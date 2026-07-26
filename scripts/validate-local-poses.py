#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.local_controller.formframe.conditioning import export_job
from services.local_controller.formframe.config import FormFrameSettings
from services.local_controller.formframe.geometry import GnmSmplxGeometry
from services.local_controller.formframe.models import Project, RenderJob


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pose_cases() -> list[tuple[str, Project]]:
    neutral = Project(name="Local validation / Neutral")
    neutral.pose.preset = "Neutral"
    neutral.pose.torso_twist = 0
    neutral.pose.head_turn = 0
    neutral.pose.head_tilt = 0
    neutral.pose.left_arm = 0
    neutral.pose.right_arm = 0
    neutral.pose.left_elbow = 0
    neutral.pose.right_elbow = 0
    neutral.pose.hip_shift = 0
    neutral.pose.left_knee = 0
    neutral.pose.right_knee = 0
    neutral.pose.expression = "Neutral"
    neutral.pose.expression_strength = 0
    neutral.pose.gaze_x = 0
    neutral.pose.gaze_y = 0

    arms_up = Project(name="Local validation / Arms up")
    arms_up.pose.preset = "Arms up"
    arms_up.pose.torso_twist = 14
    arms_up.pose.head_turn = -18
    arms_up.pose.head_tilt = 6
    arms_up.pose.left_arm = 78
    arms_up.pose.right_arm = 78
    arms_up.pose.left_elbow = 12
    arms_up.pose.right_elbow = 18
    arms_up.pose.hip_shift = 0.08
    arms_up.pose.expression = "Smile"
    arms_up.pose.expression_strength = 0.72
    arms_up.pose.gaze_x = -0.2
    arms_up.pose.gaze_y = 0.12

    stride = Project(name="Local validation / Twisted stride")
    stride.pose.preset = "Twisted stride"
    stride.pose.torso_twist = -24
    stride.pose.head_turn = 28
    stride.pose.head_tilt = -8
    stride.pose.left_arm = 42
    stride.pose.right_arm = -38
    stride.pose.left_elbow = 64
    stride.pose.right_elbow = 36
    stride.pose.hip_shift = -0.24
    stride.pose.left_knee = 28
    stride.pose.right_knee = 66
    stride.pose.expression = "Surprise"
    stride.pose.expression_strength = 0.58
    stride.pose.gaze_x = 0.35
    stride.pose.gaze_y = -0.18

    return [("neutral", neutral), ("arms-up", arms_up), ("twisted-stride", stride)]


def _contact_sheet(rows: list[tuple[str, list[Path]]], output: Path) -> None:
    opened = [
        (label, [Image.open(path).convert("RGB") for path in paths])
        for label, paths in rows
    ]
    tile_width, tile_height = opened[0][1][0].size
    label_height = 32
    sheet = Image.new(
        "RGB",
        (tile_width * 3, (tile_height + label_height) * len(opened)),
        (18, 18, 18),
    )
    draw = ImageDraw.Draw(sheet)
    column_labels = ("RGB", "DEPTH", "POSE")
    for row_index, (label, images) in enumerate(opened):
        y = row_index * (tile_height + label_height)
        for column_index, image in enumerate(images):
            if image.size != (tile_width, tile_height):
                raise ValueError("Validation passes do not share one camera resolution")
            x = column_index * tile_width
            sheet.paste(image, (x, y + label_height))
            draw.text(
                (x + 10, y + 10),
                f"{label.upper()} / {column_labels[column_index]}",
                fill=(244, 235, 214),
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "PNG", optimize=True)


def _assert_depth_covers_projected_joints(
    depth_path: Path,
    projected_joints: dict[str, list[float]],
) -> None:
    depth = Image.open(depth_path).convert("L")
    for name in ("head", "left_wrist", "right_wrist", "left_ankle", "right_ankle"):
        x, y = projected_joints[name]
        left = max(0, round(x) - 5)
        top = max(0, round(y) - 5)
        right = min(depth.width, round(x) + 6)
        bottom = min(depth.height, round(y) + 6)
        if right <= left or bottom <= top or max(depth.crop((left, top, right, bottom)).getdata()) == 0:
            raise RuntimeError(
                f"Rendered depth does not cover projected SMPL-X joint {name}"
            )


def main() -> int:
    settings = FormFrameSettings.from_environment()
    output_root = REPO_ROOT / "data" / "validation" / "real-geometry-poses"
    geometry = GnmSmplxGeometry(
        settings,
        output_root / "geometry-cache",
        settings.geometry_python,
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "provider": geometry.provider_id,
        "scope": "real-local-gnm-smplx-conditioning",
        "poses": [],
    }
    sheet_rows: list[tuple[str, list[Path]]] = []
    for label, project in _pose_cases():
        project.render.width = 512
        project.render.height = 512
        job = RenderJob(project_id=project.project_id, provider="local-preview")
        bundle, manifest, preview, result = export_job(
            project,
            job,
            output_root,
            geometry,
            create_local_result=True,
        )
        job_dir = output_root / "jobs" / job.job_id
        passes = [job_dir / "rgb.webp", job_dir / "depth.png", job_dir / "pose.png"]
        geometry_result = geometry.evaluate(project)
        projected_joints = geometry_result["projected_joints"]
        _assert_depth_covers_projected_joints(passes[1], projected_joints)
        if label == "arms-up" and not all(
            projected_joints[name][1] < projected_joints["head"][1]
            for name in ("left_wrist", "right_wrist")
        ):
            raise RuntimeError("Arms-up validation pose did not place both wrists above the head")
        sheet_rows.append((label, passes))
        report["poses"].append(
            {
                "label": label,
                "job_id": job.job_id,
                "bundle": str(bundle),
                "bundle_sha256": _sha256(bundle),
                "preview": str(preview),
                "result": str(result),
                "conditioning": {
                    path.name: {"path": str(path), "sha256": _sha256(path)}
                    for path in passes
                },
                "mesh": str(geometry_result["mesh_path"]),
                "mesh_sha256": _sha256(Path(str(geometry_result["mesh_path"]))),
                "projected_joint_count": len(projected_joints),
                "workflow_hash": manifest["workflow_hash"],
            }
        )

    sheet_path = output_root / "real-geometry-pose-contact-sheet.png"
    _contact_sheet(sheet_rows, sheet_path)
    report["contact_sheet"] = {
        "path": str(sheet_path),
        "sha256": _sha256(sheet_path),
    }
    report_path = output_root / "validation-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(
        "FORMFRAME_LOCAL_POSE_VALIDATION:"
        + json.dumps(
            {
                "pose_count": len(report["poses"]),
                "contact_sheet": str(sheet_path),
                "report": str(report_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
