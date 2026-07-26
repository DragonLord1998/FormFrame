from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Iterable, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .geometry import GeometryProvider, ProceduralGuideGeometry
from .models import Project, RenderJob


WORKFLOW_ID = "controlled-character-v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / "comfy" / "workflows" / "controlled-character-v1.api.json"
MODEL_MANIFEST_PATH = REPO_ROOT / "backend" / "colab" / "model-manifest.json"
GEOMETRY_PROVIDER = ProceduralGuideGeometry()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_versions() -> dict[str, str]:
    document = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
    return {
        "comfyui": str(document["comfyui"]["revision"]),
        "videox_fun": str(document["videox_fun"]["revision"]),
        "z_image_turbo": str(document["z_image_turbo"]["revision"]),
        "z_image_controlnet": str(document["controlnet"]["revision"]),
        "z_image_controlnet_sha256": str(document["controlnet"]["sha256"]),
        "gnm": str(document["gnm"]["revision"]),
    }


def _frame_size(project: Project) -> Tuple[int, int]:
    width = min(project.render.width, 1024)
    height = min(project.render.height, 1024)
    return width, height


def _draw_pose(project: Project, output: Path, geometry: GeometryProvider) -> None:
    width, height = _frame_size(project)
    image = Image.new("RGB", (width, height), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    points = geometry.projected_joints(project, width, height)
    bones = [
        ("head", "neck"), ("neck", "left_shoulder"), ("neck", "right_shoulder"),
        ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
        ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
        ("neck", "left_hip"), ("neck", "right_hip"), ("left_hip", "right_hip"),
        ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
        ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ]
    colors = [(244, 89, 89), (241, 185, 74), (117, 196, 145), (84, 171, 214)]
    for index, (start, end) in enumerate(bones):
        draw.line([points[start], points[end]], fill=colors[index % len(colors)], width=max(4, width // 180))
    radius = max(5, width // 120)
    for index, point in enumerate(points.values()):
        color = colors[index % len(colors)]
        draw.ellipse((point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius), fill=color)
    image.save(output, optimize=True)


def _draw_depth(project: Project, output: Path, geometry: GeometryProvider) -> None:
    width, height = _frame_size(project)
    image = Image.new("L", (width, height), 18)
    draw = ImageDraw.Draw(image)
    points = geometry.projected_joints(project, width, height)
    line_width = max(20, width // 24)
    ordered = [
        ("head", "neck"), ("neck", "left_hip"), ("neck", "right_hip"),
        ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
        ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
        ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
        ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ]
    for index, (start, end) in enumerate(ordered):
        value = max(72, 228 - index * 8)
        draw.line([points[start], points[end]], fill=value, width=line_width, joint="curve")
    head = points["head"]
    radius = line_width * 0.7
    draw.ellipse((head[0] - radius, head[1] - radius, head[0] + radius, head[1] + radius), fill=235)
    image = image.filter(ImageFilter.GaussianBlur(radius=max(2, width // 220)))
    image.save(output, optimize=True)


def _draw_rgb(project: Project, output: Path, geometry: GeometryProvider) -> None:
    width, height = _frame_size(project)
    backgrounds = {
        "Warm seamless": ("#D8CFC1", "#8D8174"),
        "Slate studio": ("#8F9698", "#42494D"),
        "Night cyclorama": ("#303238", "#13151A"),
    }
    top_color, bottom_color = backgrounds.get(project.scene.background, backgrounds["Warm seamless"])
    image = Image.new("RGB", (width, height), top_color)
    top = Image.new("RGB", (1, 1), top_color).getpixel((0, 0))
    bottom = Image.new("RGB", (1, 1), bottom_color).getpixel((0, 0))
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(round(top[index] * (1 - ratio) + bottom[index] * ratio) for index in range(3))
        for x in range(width):
            pixels[x, y] = color
    draw = ImageDraw.Draw(image, "RGBA")
    points = geometry.projected_joints(project, width, height)
    floor_y = height * 0.84
    draw.ellipse((width * 0.23, floor_y - 12, width * 0.77, floor_y + 32), fill=(20, 18, 16, 65))
    outfit = (35, 36, 38, 255) if project.character.appearance.outfit == "Studio black" else (96, 75, 55, 255)
    skin_hex = project.character.appearance.skin_tone.lstrip("#")
    skin = tuple(int(skin_hex[index:index + 2], 16) for index in (0, 2, 4)) + (255,)
    limb_width = max(18, width // 30)
    for start, end in [
        ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
        ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
        ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
        ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ]:
        draw.line([points[start], points[end]], fill=outfit, width=limb_width, joint="curve")
    torso_polygon = [
        points["left_shoulder"], points["right_shoulder"],
        points["right_hip"], points["left_hip"],
    ]
    draw.polygon(torso_polygon, fill=outfit)
    head = points["head"]
    head_radius = max(26, width // 15)
    draw.ellipse((head[0] - head_radius, head[1] - head_radius * 1.13, head[0] + head_radius, head[1] + head_radius * 1.13), fill=skin)
    hair = (32, 25, 22, 255)
    draw.pieslice((head[0] - head_radius * 1.06, head[1] - head_radius * 1.25, head[0] + head_radius * 1.06, head[1] + head_radius * 0.45), 180, 360, fill=hair)
    image = ImageEnhance.Contrast(image).enhance(1.06)
    image.save(output, "WEBP", quality=92, method=4)


def _create_preview(rgb_path: Path, preview_path: Path, final_path: Path, project: Project) -> None:
    base = Image.open(rgb_path).convert("RGB")
    bloom = base.filter(ImageFilter.GaussianBlur(radius=max(2, base.width // 180)))
    base = Image.blend(base, bloom, 0.12)
    draw = ImageDraw.Draw(base, "RGBA")
    draw.rounded_rectangle(
        (20, 20, min(base.width - 20, 330), 68),
        radius=12,
        fill=(18, 18, 18, 165),
    )
    draw.text((36, 35), "LOCAL CONDITIONING PREVIEW", fill=(248, 233, 199, 255))
    base.save(final_path, "PNG", optimize=True)
    preview = base.copy()
    preview.thumbnail((640, 640))
    preview.save(preview_path, "WEBP", quality=84, method=4)


def export_job(
    project: Project,
    job: RenderJob,
    root: Path,
    geometry: GeometryProvider | None = None,
    create_local_result: bool = True,
) -> Tuple[Path, Dict[str, object], Path, Path]:
    geometry = geometry or GEOMETRY_PROVIDER
    job_dir = root / "jobs" / job.job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    rgb_path = job_dir / "rgb.webp"
    depth_path = job_dir / "depth.png"
    pose_path = job_dir / "pose.png"
    preview_path = job_dir / "preview.webp"
    final_path = job_dir / "result.png"
    authoritative_passes = geometry.conditioning_passes(project)
    if authoritative_passes:
        shutil.copy2(authoritative_passes["rgb"], rgb_path)
        shutil.copy2(authoritative_passes["depth"], depth_path)
    else:
        _draw_rgb(project, rgb_path, geometry)
        _draw_depth(project, depth_path, geometry)
    _draw_pose(project, pose_path, geometry)
    if create_local_result:
        _create_preview(rgb_path, preview_path, final_path, project)
    assets = {}
    for key, path in (("rgb", rgb_path), ("depth", depth_path), ("pose", pose_path)):
        assets[key] = {"path": path.name, "sha256": sha256(path), "bytes": path.stat().st_size}
    reference_paths: list[Path] = []
    reference_assets: list[dict[str, object]] = []
    for reference in project.character.references:
        source = (
            root
            / "projects"
            / f"{project.project_id}.ffproject"
            / "references"
            / f"{reference.sha256}.webp"
        )
        if not source.is_file() or sha256(source) != reference.sha256:
            raise ValueError(f"Character reference {reference.role} is missing or corrupt")
        destination = job_dir / f"ref_{reference.role}_{reference.sha256[:12]}.webp"
        shutil.copy2(source, destination)
        reference_paths.append(destination)
        reference_assets.append(
            {
                "path": destination.name,
                "sha256": reference.sha256,
                "bytes": destination.stat().st_size,
                "role": reference.role,
            }
        )
    assets["references"] = reference_assets
    versions = _model_versions()
    manifest: Dict[str, object] = {
        "schema_version": 1,
        "job_id": job.job_id,
        "workflow": WORKFLOW_ID,
        "workflow_hash": sha256(WORKFLOW_PATH),
        "character_id": project.character.character_id,
        "project_id": project.project_id,
        "width": project.render.width,
        "height": project.render.height,
        "prompt": project.render.prompt,
        "negative_prompt": project.render.negative_prompt,
        "seed": project.render.seed,
        "denoise": project.render.denoise,
        "controls": {
            "depth_strength": project.render.depth_strength,
            "pose_strength": project.render.pose_strength,
            "normal_strength": 0,
            "identity_mode": "trained-lora-required" if reference_assets else "none",
        },
        "versions": {
            "geometry_provider": geometry.provider_id,
            "gnm": versions["gnm"] if geometry.provider_id == "gnm-v3-smplx" else "not-used",
            "body_model": "SMPL-X" if geometry.provider_id == "gnm-v3-smplx" else "not-used",
            "comfyui": versions["comfyui"] if job.provider == "colab" else "not-used",
            "videox_fun": versions["videox_fun"] if job.provider == "colab" else "not-used",
            "z_image_turbo": versions["z_image_turbo"] if job.provider == "colab" else "not-used",
            "z_image_controlnet": (
                versions["z_image_controlnet"] if job.provider == "colab" else "not-used"
            ),
            "z_image_controlnet_sha256": (
                versions["z_image_controlnet_sha256"]
                if job.provider == "colab"
                else "not-used"
            ),
        },
        "assets": assets,
        "output": {"preview_format": "webp", "final_format": "png"},
        "provider": job.provider,
    }
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    bundle_path = job_dir / f"{job.job_id}.ffjob"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in (manifest_path, rgb_path, depth_path, pose_path, *reference_paths):
            archive.write(path, path.name)
    return bundle_path, manifest, preview_path, final_path


def verify_bundle(bundle_path: Path) -> Iterable[str]:
    with zipfile.ZipFile(bundle_path) as archive:
        names = set(archive.namelist())
        required = {"manifest.json", "rgb.webp", "depth.png", "pose.png"}
        missing = required - names
        if missing:
            raise ValueError(f"Missing bundle files: {sorted(missing)}")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1:
            raise ValueError("Unsupported schema version")
        return sorted(names)
