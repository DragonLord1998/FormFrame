from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

BODY_JOINT = {
    "left_hip": 1,
    "right_hip": 2,
    "spine1": 3,
    "left_knee": 4,
    "right_knee": 5,
    "spine2": 6,
    "left_ankle": 7,
    "right_ankle": 8,
    "spine3": 9,
    "neck": 12,
    "head": 15,
    "left_shoulder": 16,
    "right_shoulder": 17,
    "left_elbow": 18,
    "right_elbow": 19,
    "left_wrist": 20,
    "right_wrist": 21,
}


def _resolve_smplx_model_file(model_dir: Path) -> tuple[Path, str]:
    """Accept either the SMPL-X leaf directory or its parent."""
    for candidate in (model_dir, model_dir / "smplx", model_dir / "SMPLX"):
        for extension in ("npz", "pkl"):
            model_file = candidate / f"SMPLX_NEUTRAL.{extension}"
            if model_file.is_file():
                return model_file, extension
    raise FileNotFoundError(
        "Missing SMPLX_NEUTRAL.npz or SMPLX_NEUTRAL.pkl in "
        f"{model_dir}, {model_dir / 'smplx'}, or {model_dir / 'SMPLX'}"
    )


def _axis_angle(degrees: float, axis: int) -> np.ndarray:
    value = np.zeros(3, dtype=np.float32)
    value[axis] = math.radians(degrees)
    return value


def _fit(values: list[float], length: int, scale: float = 1.0) -> np.ndarray:
    result = np.zeros(length, dtype=np.float32)
    count = min(len(values), length)
    result[:count] = np.asarray(values[:count], dtype=np.float32) * scale
    return result


def _smplx_pose(project: dict[str, Any]) -> np.ndarray:
    pose = project["pose"]
    body = np.zeros((21, 3), dtype=np.float32)
    body[2] = _axis_angle(float(pose["torso_twist"]), 1)
    body[11] = _axis_angle(float(pose["head_turn"]) * 0.35, 1)
    body[11] += _axis_angle(float(pose["head_tilt"]) * 0.35, 2)
    body[14] = _axis_angle(float(pose["head_turn"]) * 0.65, 1)
    body[14] += _axis_angle(float(pose["head_tilt"]) * 0.65, 2)
    body[15] = _axis_angle(float(pose["left_arm"]), 2)
    body[16] = _axis_angle(-float(pose["right_arm"]), 2)
    body[17] = _axis_angle(float(pose["left_elbow"]), 1)
    body[18] = _axis_angle(-float(pose["right_elbow"]), 1)
    body[3] = _axis_angle(float(pose["left_knee"]), 0)
    body[4] = _axis_angle(float(pose["right_knee"]), 0)
    body[0, 2] += float(pose["hip_shift"]) * 0.2
    return body.reshape(1, -1)


def _load_smplx(project: dict[str, Any], model_dir: Path):
    import smplx
    import torch

    character = project["character"]
    betas = _fit(character.get("body_shape", []), 10, 0.65)[None, :]
    model_file, extension = _resolve_smplx_model_file(model_dir)
    model = smplx.create(
        str(model_file),
        model_type="smplx",
        gender="neutral",
        use_pca=False,
        flat_hand_mean=True,
        num_betas=10,
        ext=extension,
    )
    with torch.no_grad():
        output = model(
            betas=torch.tensor(betas),
            body_pose=torch.tensor(_smplx_pose(project)),
            global_orient=torch.zeros((1, 3)),
            transl=torch.tensor([[float(project["pose"]["hip_shift"]) * 0.05, 0, 0]]),
            return_full_pose=True,
        )
    vertices = output.vertices[0].detach().cpu().numpy()
    joints = output.joints[0].detach().cpu().numpy()
    height_scale = float(character.get("height", 1.0))
    vertices[:, 1] *= height_scale
    joints[:, 1] *= height_scale
    floor = float(vertices[:, 1].min())
    vertices[:, 1] -= floor
    joints[:, 1] -= floor
    return model, vertices, joints


def _gnm_expression(model, project: dict[str, Any]) -> np.ndarray:
    pose = project["pose"]
    strength = float(pose.get("expression_strength", 0))
    expression = np.zeros(model.expression_dim, dtype=np.float32)
    label = str(pose.get("expression", "")).lower()
    tokens = {
        "confidence": ("mouth", "lip", "brow"),
        "smile": ("smile", "lip", "cheek"),
        "surprise": ("jaw", "eye", "brow"),
        "neutral": (),
    }
    wanted = next((value for key, value in tokens.items() if key in label), ("mouth", "brow"))
    for index, name in enumerate(model.expression_names):
        lowered = str(name).lower()
        if any(token in lowered for token in wanted):
            expression[index] = strength * 0.35
    return expression


def _orthonormal_head_frame(joints: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    neck = joints[BODY_JOINT["neck"]]
    head = joints[BODY_JOINT["head"]]
    left = joints[BODY_JOINT["left_shoulder"]]
    right = joints[BODY_JOINT["right_shoulder"]]
    y_axis = head - neck
    y_axis /= max(float(np.linalg.norm(y_axis)), 1e-8)
    x_axis = right - left
    x_axis -= y_axis * float(np.dot(x_axis, y_axis))
    x_axis /= max(float(np.linalg.norm(x_axis)), 1e-8)
    z_axis = np.cross(x_axis, y_axis)
    z_axis /= max(float(np.linalg.norm(z_axis)), 1e-8)
    rotation = np.stack([x_axis, y_axis, z_axis], axis=1)
    neck_head = float(np.linalg.norm(head - neck))
    return head, rotation, neck_head


def _load_gnm(project: dict[str, Any], smplx_joints: np.ndarray):
    from gnm.shape import gnm_numpy

    model = gnm_numpy.GNM.from_local(
        version=gnm_numpy.GNMMajorVersion.V3,
        variant=gnm_numpy.GNMVariant.HEAD,
    )
    identity = _fit(project["character"].get("identity", []), model.identity_dim)
    expression = _gnm_expression(model, project)
    # SMPL-X owns the neck/head transform. GNM only applies local eye rotations.
    rotations = np.zeros((model.num_joints, 3), dtype=np.float32)
    joint_names = {str(name): index for index, name in enumerate(model.joint_names)}
    gaze_x = float(project["pose"].get("gaze_x", 0))
    gaze_y = float(project["pose"].get("gaze_y", 0))
    for eye in ("left_eye", "right_eye"):
        if eye in joint_names:
            rotations[joint_names[eye], 0] = -gaze_y * 0.32
            rotations[joint_names[eye], 1] = gaze_x * 0.42
    vertices = np.asarray(
        model(
            identity=identity,
            expression=expression,
            rotations=rotations,
            translation=np.zeros(3, dtype=np.float32),
        )
    )
    center = (vertices.min(axis=0) + vertices.max(axis=0)) * 0.5
    height = max(float(np.ptp(vertices[:, 1])), 1e-8)
    head, rotation, neck_head = _orthonormal_head_frame(smplx_joints)
    scale = (neck_head * 3.25) / height
    aligned = ((vertices - center) * scale) @ rotation.T
    aligned += head
    return model, aligned


def _normalize(vector: np.ndarray) -> np.ndarray:
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


def _camera_frame(
    project: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    scene = project["scene"]
    alpha = math.radians(90 + float(scene["camera_yaw"]))
    beta = math.radians(80 - float(scene["camera_pitch"]))
    radius = float(scene["camera_distance"])
    target = np.asarray([0.0, 1.5, 0.0], dtype=np.float32)
    position = target + radius * np.asarray(
        [
            math.cos(alpha) * math.sin(beta),
            math.cos(beta),
            math.sin(alpha) * math.sin(beta),
        ],
        dtype=np.float32,
    )
    forward = _normalize(target - position)
    right = _normalize(np.cross(forward, np.asarray([0.0, 1.0, 0.0], dtype=np.float32)))
    up = _normalize(np.cross(right, forward))
    vertical_fov = 2 * math.atan(18 / float(scene["focal_length"]))
    return position, right, up, forward, vertical_fov


def _project_vertices(
    vertices: np.ndarray,
    project: dict[str, Any],
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray]:
    position, right, up, forward, vertical_fov = _camera_frame(project)
    relative = vertices - position
    depth = relative @ forward
    focal_pixels = height * 0.5 / math.tan(vertical_fov * 0.5)
    safe_depth = np.maximum(depth, 1e-5)
    projected = np.column_stack(
        [
            width * 0.5 + (relative @ right) * focal_pixels / safe_depth,
            height * 0.5 - (relative @ up) * focal_pixels / safe_depth,
        ]
    )
    return projected, depth


def _project_joints(
    joints: np.ndarray,
    project: dict[str, Any],
    width: int,
    height: int,
) -> dict[str, list[float]]:
    screen, depth = _project_vertices(joints, project, width, height)
    projected: dict[str, list[float]] = {}
    for name, index in BODY_JOINT.items():
        if depth[index] <= 0:
            raise RuntimeError(f"SMPL-X joint {name} is behind the active camera")
        projected[name] = [float(screen[index, 0]), float(screen[index, 1])]
    return projected


def _connector_mesh(
    joints: np.ndarray,
    neck_head: float,
    segments: int = 24,
) -> tuple[np.ndarray, np.ndarray]:
    neck = joints[BODY_JOINT["neck"]]
    head = joints[BODY_JOINT["head"]]
    axis = _normalize(head - neck)
    reference = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)
    if abs(float(np.dot(axis, reference))) > 0.9:
        reference = np.asarray([0.0, 0.0, 1.0], dtype=np.float32)
    tangent = _normalize(np.cross(axis, reference))
    bitangent = _normalize(np.cross(axis, tangent))
    centers = (
        neck + axis * neck_head * 0.05,
        neck + axis * neck_head * 0.42,
        neck + axis * neck_head * 0.72,
    )
    radii = (neck_head * 0.48, neck_head * 0.44, neck_head * 0.40)
    vertices = []
    for center, radius in zip(centers, radii):
        for index in range(segments):
            angle = 2 * math.pi * index / segments
            vertices.append(
                center
                + radius * (math.cos(angle) * tangent + math.sin(angle) * bitangent)
            )
    faces = []
    for ring in range(len(centers) - 1):
        offset = ring * segments
        next_offset = (ring + 1) * segments
        for index in range(segments):
            following = (index + 1) % segments
            faces.append([offset + index, next_offset + index, next_offset + following])
            faces.append([offset + index, next_offset + following, offset + following])
    return np.asarray(vertices, dtype=np.float32), np.asarray(faces, dtype=np.int64)


def _trim_smplx_head(
    vertices: np.ndarray,
    faces: np.ndarray,
    joints: np.ndarray,
) -> np.ndarray:
    neck = joints[BODY_JOINT["neck"]]
    head = joints[BODY_JOINT["head"]]
    axis = _normalize(head - neck)
    centroids = vertices[faces].mean(axis=1)
    along_neck = (centroids - neck) @ axis
    return faces[along_neck <= float(np.linalg.norm(head - neck)) * 0.48]


def _hex_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def _render_conditioning(
    project: dict[str, Any],
    meshes: list[tuple[np.ndarray, np.ndarray, tuple[int, int, int]]],
    output_dir: Path,
    width: int,
    height: int,
) -> tuple[Path, Path]:
    backgrounds = {
        "Warm seamless": ("#D8CFC1", "#8D8174"),
        "Slate studio": ("#8F9698", "#42494D"),
        "Night cyclorama": ("#303238", "#13151A"),
    }
    top_hex, bottom_hex = backgrounds.get(
        project["scene"].get("background", "Warm seamless"),
        backgrounds["Warm seamless"],
    )
    top = np.asarray(_hex_rgb(top_hex), dtype=np.float32)
    bottom = np.asarray(_hex_rgb(bottom_hex), dtype=np.float32)
    rows = np.linspace(top, bottom, height, dtype=np.uint8)
    rgb_array = np.repeat(rows[:, None, :], width, axis=1)
    rgb = Image.fromarray(rgb_array, mode="RGB")
    depth_image = Image.new("L", (width, height), 0)
    rgb_draw = ImageDraw.Draw(rgb)
    depth_draw = ImageDraw.Draw(depth_image)
    position, _, _, _, _ = _camera_frame(project)
    light_direction = _normalize(np.asarray([-0.65, 1.0, -0.5], dtype=np.float32))
    projected_meshes = []
    all_depths = []
    for vertices, faces, color in meshes:
        screen, depth = _project_vertices(vertices, project, width, height)
        projected_meshes.append((vertices, faces, color, screen, depth))
        all_depths.append(depth[depth > 0])
    visible_depths = np.concatenate(all_depths)
    near = float(np.percentile(visible_depths, 1))
    far = float(np.percentile(visible_depths, 99))
    span = max(far - near, 1e-5)
    polygons = []
    for vertices, faces, color, screen, depth in projected_meshes:
        triangles = vertices[faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        normal_lengths = np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-8)
        normals /= normal_lengths
        centers = triangles.mean(axis=1)
        to_camera = position - centers
        facing = np.sum(normals * to_camera, axis=1)
        for index, face in enumerate(faces):
            face_depth = depth[face]
            if np.any(face_depth <= 0) or facing[index] <= 0:
                continue
            points = screen[face]
            if (
                points[:, 0].max() < 0
                or points[:, 0].min() >= width
                or points[:, 1].max() < 0
                or points[:, 1].min() >= height
            ):
                continue
            mean_depth = float(face_depth.mean())
            light = 0.42 + 0.58 * max(0.0, float(np.dot(normals[index], light_direction)))
            shaded = tuple(max(0, min(255, round(channel * light))) for channel in color)
            depth_value = max(1, min(255, round(255 * (far - mean_depth) / span)))
            polygons.append(
                (
                    mean_depth,
                    [tuple(map(float, point)) for point in points],
                    shaded,
                    depth_value,
                )
            )
    for _, points, color, depth_value in sorted(polygons, key=lambda item: item[0], reverse=True):
        rgb_draw.polygon(points, fill=color)
        depth_draw.polygon(points, fill=depth_value)
    rgb_path = output_dir / "conditioning-rgb.webp"
    depth_path = output_dir / "conditioning-depth.png"
    rgb.save(rgb_path, "WEBP", quality=92, method=4)
    depth_image.save(depth_path, "PNG", optimize=True)
    return rgb_path, depth_path


def evaluate(project: dict[str, Any], model_dir: Path, output_dir: Path) -> dict[str, Any]:
    import trimesh

    body_model, body_vertices, joints = _load_smplx(project, model_dir)
    gnm_model, head_vertices = _load_gnm(project, joints)
    output_dir.mkdir(parents=True, exist_ok=True)
    body_faces = _trim_smplx_head(
        body_vertices,
        np.asarray(body_model.faces, dtype=np.int64),
        joints,
    )
    _, _, neck_head = _orthonormal_head_frame(joints)
    connector_vertices, connector_faces = _connector_mesh(joints, neck_head)
    skin_color = _hex_rgb(project["character"]["appearance"]["skin_tone"])
    outfit_colors = {
        "Studio black": (36, 37, 35),
        "Field jacket": (74, 85, 64),
        "Bone tailoring": (212, 199, 178),
    }
    outfit_color = outfit_colors.get(
        project["character"]["appearance"].get("outfit", "Studio black"),
        outfit_colors["Studio black"],
    )
    body_mesh = trimesh.Trimesh(
        vertices=body_vertices,
        faces=body_faces,
        process=False,
        vertex_colors=(*outfit_color, 255),
    )
    head_mesh = trimesh.Trimesh(
        vertices=head_vertices,
        faces=np.asarray(gnm_model.triangles, dtype=np.int64),
        process=False,
        vertex_colors=(*skin_color, 255),
    )
    connector_mesh = trimesh.Trimesh(
        vertices=connector_vertices,
        faces=connector_faces,
        process=False,
        vertex_colors=(*skin_color, 255),
    )
    scene = trimesh.Scene()
    scene.add_geometry(body_mesh, node_name="smplx_body", geom_name="smplx_body")
    scene.add_geometry(head_mesh, node_name="gnm_head", geom_name="gnm_head")
    scene.add_geometry(
        connector_mesh,
        node_name="neck_connector",
        geom_name="neck_connector",
    )
    glb_path = output_dir / "character.glb"
    glb_path.write_bytes(scene.export(file_type="glb"))
    width = int(project["render"]["width"])
    height = int(project["render"]["height"])
    rgb_path, depth_path = _render_conditioning(
        project,
        [
            (body_vertices, body_faces, outfit_color),
            (head_vertices, np.asarray(gnm_model.triangles, dtype=np.int64), skin_color),
            (connector_vertices, connector_faces, skin_color),
        ],
        output_dir,
        width,
        height,
    )
    document = {
        "schema_version": 1,
        "provider": "gnm-v3-smplx",
        "mesh_path": str(glb_path),
        "conditioning_rgb_path": str(rgb_path),
        "conditioning_depth_path": str(depth_path),
        "projected_joints": _project_joints(joints, project, width, height),
        "camera": {
            "yaw": float(project["scene"]["camera_yaw"]),
            "pitch": float(project["scene"]["camera_pitch"]),
            "distance": float(project["scene"]["camera_distance"]),
            "focal_length": float(project["scene"]["focal_length"]),
            "target": [0.0, 1.5, 0.0],
        },
        "ownership": {
            "body_pose": "smplx",
            "global_head_pose": "smplx",
            "head_identity": "gnm",
            "facial_expression": "gnm",
            "gaze": "gnm",
        },
        "counts": {
            "smplx_vertices": int(body_vertices.shape[0]),
            "smplx_visible_faces": int(body_faces.shape[0]),
            "gnm_vertices": int(head_vertices.shape[0]),
            "connector_vertices": int(connector_vertices.shape[0]),
        },
    }
    result_path = output_dir / "geometry.json"
    result_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--smplx-model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    project = json.loads(args.project.read_text())
    result = evaluate(project, args.smplx_model_dir, args.output_dir)
    print("FORMFRAME_GEOMETRY_JSON:" + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
