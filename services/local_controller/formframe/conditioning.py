from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Iterable, Sequence, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .geometry import GeometryProvider, ProceduralGuideGeometry
from .models import Project, RenderJob


WORKFLOW_ID = "controlled-character-v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / "comfy" / "workflows" / "controlled-character-v1.api.json"
MODEL_MANIFEST_PATH = REPO_ROOT / "backend" / "colab" / "model-manifest.json"
GEOMETRY_PROVIDER = ProceduralGuideGeometry()
BENCHMARK_VARIANTS = ("A", "B", "C", "D", "E", "F")


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
    outfit_colors = {
        "Studio black": (35, 36, 38, 255),
        "Field jacket": (74, 85, 64, 255),
        "Bone tailoring": (212, 199, 178, 255),
    }
    outfit = outfit_colors.get(
        project.character.appearance.outfit,
        outfit_colors["Studio black"],
    )
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
    proxy_margin = {
        "garment_proxy_studio_black": 0,
        "garment_proxy_field_jacket": max(10, width // 45),
        "garment_proxy_bone_tailoring": max(6, width // 70),
    }.get(project.character.appearance.garment_proxy, 0)
    torso_polygon = [
        (points["left_shoulder"][0] - proxy_margin, points["left_shoulder"][1]),
        (points["right_shoulder"][0] + proxy_margin, points["right_shoulder"][1]),
        (points["right_hip"][0] + proxy_margin // 2, points["right_hip"][1]),
        (points["left_hip"][0] - proxy_margin // 2, points["left_hip"][1]),
    ]
    draw.polygon(torso_polygon, fill=outfit)
    head = points["head"]
    head_radius = max(26, width // 15)
    draw.ellipse((head[0] - head_radius, head[1] - head_radius * 1.13, head[0] + head_radius, head[1] + head_radius * 1.13), fill=skin)
    hair = (32, 25, 22, 255)
    hair_shape = {
        "hair_proxy_sculpted_crop": (1.06, 1.25, 1.06, 0.45),
        "hair_proxy_soft_bob": (1.24, 1.2, 1.24, 1.02),
        "hair_proxy_pulled_back": (1.02, 1.2, 1.18, 0.5),
    }.get(project.character.appearance.hair_proxy, (1.06, 1.25, 1.06, 0.45))
    left_scale, top_scale, right_scale, bottom_scale = hair_shape
    draw.pieslice(
        (
            head[0] - head_radius * left_scale,
            head[1] - head_radius * top_scale,
            head[0] + head_radius * right_scale,
            head[1] + head_radius * bottom_scale,
        ),
        180,
        360,
        fill=hair,
    )
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


def _create_contact_sheet(passes: Sequence[tuple[str, Path]], output: Path) -> None:
    images = [(label, Image.open(path).convert("RGB")) for label, path in passes]
    if not images:
        raise ValueError("At least one conditioning pass is required")
    width, height = images[0][1].size
    if any(image.size != (width, height) for _, image in images):
        raise ValueError("Conditioning passes must share dimensions")

    label_height = max(24, height // 28)
    sheet = Image.new("RGB", (width * len(images), height + label_height), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    for index, (label, image) in enumerate(images):
        x = index * width
        sheet.paste(image, (x, label_height))
        draw.rectangle((x, 0, x + width - 1, label_height - 1), fill=(18, 18, 18))
        draw.text((x + 12, max(5, label_height // 2 - 5)), label.upper(), fill=(244, 235, 214))
    sheet.save(output, "PNG", optimize=True)


def build_comparison_matrix_manifest(
    job_manifest: Dict[str, object],
    *,
    variants: Sequence[str] = BENCHMARK_VARIANTS,
) -> Dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "formframe-a100-comparison-matrix",
        "workflow": WORKFLOW_ID,
        "source_job_id": job_manifest["job_id"],
        "source_project_id": job_manifest["project_id"],
        "conditioning_contract": job_manifest["schema_version"],
        "evidence_boundary": (
            "Local conditioning previews and contact sheets validate exported controls only; "
            "comparison rows are not live render evidence until populated from an A100 result manifest."
        ),
        "required_live_evidence": [
            "a100_result_manifest",
            "result_png_sha256",
            "preview_webp_sha256",
            "runtime_gpu_probe",
            "workflow_hash",
            "model_manifest_sha256",
        ],
        "variants": [
            {
                "variant": variant,
                "status": "pending-live-a100",
                "a100_result_manifest": None,
                "result_png_sha256": None,
                "preview_webp_sha256": None,
                "notes": "",
            }
            for variant in variants
        ],
    }


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
    normal_path = job_dir / "normal.png"
    contact_sheet_path = job_dir / "conditioning-contact-sheet.png"
    preview_path = job_dir / "preview.webp"
    final_path = job_dir / "result.png"
    if normal_path.is_file():
        normal_path.unlink()
    authoritative_passes = geometry.conditioning_passes(project)
    if authoritative_passes:
        shutil.copy2(authoritative_passes["rgb"], rgb_path)
        shutil.copy2(authoritative_passes["depth"], depth_path)
        if authoritative_passes.get("normal"):
            shutil.copy2(authoritative_passes["normal"], normal_path)
    else:
        _draw_rgb(project, rgb_path, geometry)
        _draw_depth(project, depth_path, geometry)
    _draw_pose(project, pose_path, geometry)
    conditioning_passes = [("rgb", rgb_path), ("depth", depth_path), ("pose", pose_path)]
    if normal_path.is_file():
        conditioning_passes.append(("normal", normal_path))
    _create_contact_sheet(conditioning_passes, contact_sheet_path)
    if create_local_result:
        _create_preview(rgb_path, preview_path, final_path, project)
    assets = {}
    for key, path in conditioning_passes:
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
    identity_lora = project.character.identity_lora
    if identity_lora:
        local_lora = root / "assets" / identity_lora.sha256
        if (
            not local_lora.is_file()
            or local_lora.stat().st_size != identity_lora.bytes
            or sha256(local_lora) != identity_lora.sha256
        ):
            raise ValueError("Attached identity LoRA is missing or corrupt")
        assets["identity_lora"] = {
            "path": f"formframe_{identity_lora.sha256}.safetensors",
            "sha256": identity_lora.sha256,
            "bytes": identity_lora.bytes,
        }
    versions = _model_versions()
    prompt = project.render.prompt
    if identity_lora:
        prompt = f"{identity_lora.trigger_token}, {prompt}"
    manifest: Dict[str, object] = {
        "schema_version": 1,
        "job_id": job.job_id,
        "workflow": WORKFLOW_ID,
        "workflow_hash": sha256(WORKFLOW_PATH),
        "character_id": project.character.character_id,
        "project_id": project.project_id,
        "width": project.render.width,
        "height": project.render.height,
        "prompt": prompt,
        "negative_prompt": project.render.negative_prompt,
        "seed": project.render.seed,
        "denoise": project.render.denoise,
        "controls": {
            "depth_strength": project.render.depth_strength,
            "pose_strength": project.render.pose_strength,
            "normal_strength": 0,
            "identity_mode": (
                "trained-lora"
                if identity_lora
                else "references-awaiting-training"
                if reference_assets
                else "none"
            ),
            "identity_lora_strength": identity_lora.strength if identity_lora else 0,
            "identity_trigger_token": identity_lora.trigger_token if identity_lora else "",
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
        "output": {
            "preview_format": "webp",
            "final_format": "png",
            "local_validation": {
                "path": contact_sheet_path.name,
                "sha256": sha256(contact_sheet_path),
                "bytes": contact_sheet_path.stat().st_size,
                "passes": [label for label, _ in conditioning_passes],
                "evidence_scope": "local-conditioning-export-only",
            },
        },
        "provider": job.provider,
    }
    manifest_path = job_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    bundle_path = job_dir / f"{job.job_id}.ffjob"
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_STORED) as archive:
        bundle_inputs = [manifest_path, rgb_path, depth_path, pose_path]
        if normal_path.is_file():
            bundle_inputs.append(normal_path)
        for path in (*bundle_inputs, *reference_paths):
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
