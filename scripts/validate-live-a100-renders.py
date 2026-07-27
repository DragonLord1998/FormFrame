#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.local_controller.formframe.conditioning import export_job
from services.local_controller.formframe.config import FormFrameSettings
from services.local_controller.formframe.geometry import GnmSmplxGeometry
from services.local_controller.formframe.models import Project, RenderJob
from services.local_controller.formframe.remote import ColabRemoteRuntime


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pose_cases() -> list[tuple[str, Project]]:
    neutral = Project(name="Live A100 / Neutral")
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

    arms_up = Project(name="Live A100 / Arms up")
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

    stride = Project(name="Live A100 / Twisted stride")
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

    for project in (neutral, arms_up, stride):
        project.render.width = 768
        project.render.height = 1024
        project.render.depth_strength = 0.85
        project.render.pose_strength = 0.65
        project.render.negative_prompt = (
            "mannequin, skeleton, doll, robot, metallic body, cropped body, close-up, "
            "headshot, distorted anatomy, duplicate limbs, plastic skin"
        )
        project.render.quality = "Final"
    neutral.render.prompt = (
        "Photorealistic full-body studio photograph of an adult woman with natural skin "
        "wearing a fitted black outfit, entire figure visible head to toe, standing in a "
        "neutral open pose, centered on a plain seamless background"
    )
    arms_up.render.prompt = (
        "Photorealistic full-body studio photograph of an adult woman with natural skin "
        "wearing a fitted black outfit, entire figure visible head to toe, both arms "
        "raised overhead, centered on a plain seamless background"
    )
    stride.render.prompt = (
        "Photorealistic full-body studio photograph of an adult woman with natural skin "
        "wearing a fitted black outfit, entire figure visible head to toe, dynamic "
        "twisted walking stride, centered on a plain seamless background"
    )
    return [("neutral", neutral), ("arms-up", arms_up), ("twisted-stride", stride)]


def _contact_sheet(results: list[tuple[str, Path]], output: Path) -> None:
    opened = [(label, Image.open(path).convert("RGB")) for label, path in results]
    tile_width, tile_height = opened[0][1].size
    label_height = 40
    sheet = Image.new(
        "RGB",
        (tile_width * len(opened), tile_height + label_height),
        (18, 18, 18),
    )
    draw = ImageDraw.Draw(sheet)
    for column, (label, image) in enumerate(opened):
        if image.size != (tile_width, tile_height):
            raise RuntimeError("Live A100 results do not share one resolution")
        x = column * tile_width
        sheet.paste(image, (x, label_height))
        draw.text((x + 12, 13), label.upper(), fill=(244, 235, 214))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, "PNG", optimize=True)


def _download_logs(runtime: ColabRemoteRuntime, output_root: Path) -> dict[str, Any]:
    logs_root = output_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    for name in ("bootstrap", "comfyui", "gateway", "cloudflared"):
        destination = logs_root / f"{name}.log"
        try:
            runtime.cli.download(
                f"/content/formframe/logs/{name}.log",
                destination,
                timeout_seconds=180,
            )
            report[name] = {
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        except Exception as exc:
            report[name] = {"error": str(exc)}
    return report


def main() -> int:
    settings = FormFrameSettings.from_environment()
    errors = settings.remote_readiness_errors()
    if errors:
        raise RuntimeError("; ".join(errors))
    output_root = REPO_ROOT / "data" / "validation" / "live-a100"
    output_root.mkdir(parents=True, exist_ok=True)
    runtime = ColabRemoteRuntime(settings, REPO_ROOT)
    geometry = GnmSmplxGeometry(
        settings,
        output_root / "geometry-cache",
        settings.geometry_python,
    )
    if runtime.cli.session_active():
        raise RuntimeError(
            f"Refusing to take ownership of existing Colab session {settings.colab_session}"
        )

    runtime_ready = False
    report: dict[str, Any] = {
        "schema_version": 1,
        "repository_revision": settings.github_revision,
        "session": settings.colab_session,
        "poses": [],
    }
    rendered: list[tuple[str, Path]] = []
    started_at = time.monotonic()
    try:
        def startup_progress(
            status: str,
            label: str,
            percent: int,
            detail: str,
        ) -> None:
            print(
                "FORMFRAME_LIVE_PROGRESS:"
                + json.dumps(
                    {
                        "status": status,
                        "label": label,
                        "percent": percent,
                        "detail": detail,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

        health = runtime.start(startup_progress)
        runtime_ready = True
        report["health"] = {
            key: value
            for key, value in health.items()
            if key != "gateway_url"
        }
        for index, (label, project) in enumerate(_pose_cases(), start=1):
            job = RenderJob(project_id=project.project_id, provider="colab")
            bundle, manifest, _preview, _result = export_job(
                project,
                job,
                output_root,
                geometry,
                create_local_result=False,
            )
            job_dir = output_root / "jobs" / job.job_id

            def render_progress(percent: int, stage: str) -> None:
                print(
                    "FORMFRAME_RENDER_PROGRESS:"
                    + json.dumps(
                        {
                            "pose": label,
                            "index": index,
                            "total": 3,
                            "percent": percent,
                            "stage": stage,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

            result = runtime.render(
                job.job_id,
                bundle,
                job_dir,
                render_progress,
            )
            rendered.append((label, result.result_path))
            report["poses"].append(
                {
                    "label": label,
                    "job_id": job.job_id,
                    "result": str(result.result_path),
                    "result_sha256": _sha256(result.result_path),
                    "preview": str(result.preview_path),
                    "preview_sha256": _sha256(result.preview_path),
                    "bundle": str(bundle),
                    "bundle_bytes": bundle.stat().st_size,
                    "bundle_sha256": _sha256(bundle),
                    "workflow_hash": manifest["workflow_hash"],
                    "remote_result": result.result_manifest,
                    "transfer_plan": result.transfer_plan,
                    "cli_fallback_used": result.used_cli_fallback,
                }
            )
        sheet = output_root / "live-a100-contact-sheet.png"
        _contact_sheet(rendered, sheet)
        report["contact_sheet"] = {
            "path": str(sheet),
            "sha256": _sha256(sheet),
        }
        report["logs"] = _download_logs(runtime, output_root)
        report["elapsed_seconds"] = time.monotonic() - started_at
        report_path = output_root / "validation-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            "FORMFRAME_LIVE_A100_VALIDATION:"
            + json.dumps(
                {
                    "pose_count": len(rendered),
                    "contact_sheet": str(sheet),
                    "report": str(report_path),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    finally:
        if runtime_ready:
            runtime.stop()
            print(
                "FORMFRAME_A100_STOPPED:"
                + json.dumps({"session": settings.colab_session}, sort_keys=True),
                flush=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
