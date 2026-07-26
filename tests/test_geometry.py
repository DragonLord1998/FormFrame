import json
import subprocess
from pathlib import Path

import pytest

from services.local_controller.formframe.geometry import (
    ProceduralGuideGeometry,
    UnconfiguredModelGeometry,
    geometry_cache_digest,
)
from services.local_controller.formframe.models import Project


def test_geometry_provider_is_deterministic_and_fail_closed():
    project = Project()
    provider = ProceduralGuideGeometry()
    first = provider.projected_joints(project, 768, 1024)
    second = provider.projected_joints(project, 768, 1024)

    assert first == second
    assert {"head", "neck", "left_wrist", "right_wrist", "left_ankle", "right_ankle"} <= first.keys()
    with pytest.raises(RuntimeError, match="not configured"):
        UnconfiguredModelGeometry().projected_joints(project, 768, 1024)


def test_geometry_cache_is_invalidated_when_worker_source_changes():
    project = Project()
    assert geometry_cache_digest(project, "worker-a") != geometry_cache_digest(
        project,
        "worker-b",
    )


def test_full_gnm_and_smplx_coefficient_vectors_reach_geometry_math():
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    project = Project()
    project.pose.expression = "Surprised"
    project.pose.expression_strength = 1
    project.pose.gnm_expression[0] = 0.5
    project.pose.gnm_expression[17] = 0.75
    project.pose.gnm_joint_rotations[0] = 30
    project.pose.gnm_joint_rotations[6] = 10
    project.pose.gnm_joint_rotations[7] = 5
    project.pose.gaze_x = 0.25
    project.pose.gaze_y = 0.5
    project.pose.smplx_body_pose[0] = 15
    project.pose.smplx_body_pose[62] = -22
    script = r"""
import json
import sys

from services.geometry_worker.main import _gnm_expression, _gnm_rotations, _smplx_pose

class FakeGnm:
    expression_dim = 383
    expression_names = ["surprise_eye", *[f"basis_{index:03d}" for index in range(1, 383)]]
    num_joints = 4
    joint_names = ["neck", "head", "left_eye", "right_eye"]

project = json.loads(sys.argv[1])
expression = _gnm_expression(FakeGnm(), project)
rotations = _gnm_rotations(FakeGnm(), project)
body_pose = _smplx_pose(project).reshape(21, 3)
print(json.dumps({
    "expression_size": int(expression.size),
    "expression_0": float(expression[0]),
    "expression_17": float(expression[17]),
    "neck_rotation": rotations[0].tolist(),
    "left_eye_rotation": rotations[2].tolist(),
    "body_first": float(body_pose[0, 0]),
    "body_last": float(body_pose[20, 2]),
}))
"""
    completed = subprocess.run(
        [str(worker_python), "-c", script, project.model_dump_json()],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["expression_size"] == 383
    assert result["expression_0"] == pytest.approx(0.85)
    assert result["expression_17"] == pytest.approx(0.75)
    assert result["neck_rotation"] == pytest.approx([0, 0, 0])
    assert result["left_eye_rotation"][0] == pytest.approx(0.0145329, rel=1e-5)
    assert result["left_eye_rotation"][1] == pytest.approx(0.192266, rel=1e-5)
    assert result["body_first"] == pytest.approx(0.261799, rel=1e-5)
    assert result["body_last"] == pytest.approx(-0.383972, rel=1e-5)


def test_geometry_worker_accepts_smplx_leaf_or_parent_layout(tmp_path: Path):
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    leaf = tmp_path / "smplx"
    leaf.mkdir()
    model_file = leaf / "SMPLX_NEUTRAL.npz"
    model_file.write_bytes(b"placeholder")
    script = r"""
import json
import sys
from pathlib import Path

from services.geometry_worker.main import _resolve_smplx_model_file

parent_file, parent_ext = _resolve_smplx_model_file(Path(sys.argv[1]))
leaf_file, leaf_ext = _resolve_smplx_model_file(Path(sys.argv[2]))
print(json.dumps({
    "parent_file": parent_file.name,
    "parent_ext": parent_ext,
    "leaf_file": leaf_file.name,
    "leaf_ext": leaf_ext,
}))
"""
    completed = subprocess.run(
        [
            str(worker_python),
            "-c",
            script,
            str(tmp_path),
            str(leaf),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "parent_file": "SMPLX_NEUTRAL.npz",
        "parent_ext": "npz",
        "leaf_file": "SMPLX_NEUTRAL.npz",
        "leaf_ext": "npz",
    }


def test_geometry_worker_camera_and_conditioning_contract(tmp_path: Path):
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    project = Project()
    project.render.width = 512
    project.render.height = 512
    project.scene.camera_yaw = 0
    project.scene.camera_pitch = 0
    script = r"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from services.geometry_worker.main import _project_vertices, _render_conditioning

project = json.loads(sys.argv[1])
output = Path(sys.argv[2])
points = np.asarray([[-0.5, 1.5, 0.0], [0.5, 1.5, 0.0]], dtype=np.float32)
normal, camera_depth = _project_vertices(points, project, 512, 512)
project["scene"]["focal_length"] = 105
telephoto, _ = _project_vertices(points, project, 512, 512)
vertices = np.asarray([
    [-0.5, 0.6, -0.5], [0.5, 0.6, -0.5],
    [0.5, 1.6, -0.5], [-0.5, 1.6, -0.5],
    [-0.5, 0.6, 0.5], [0.5, 0.6, 0.5],
    [0.5, 1.6, 0.5], [-0.5, 1.6, 0.5],
], dtype=np.float32)
faces = np.asarray([
    [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
    [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6],
    [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3],
], dtype=np.int64)
rgb, depth = _render_conditioning(
    project, [(vertices, faces, (180, 120, 90))], output, 512, 512
)
print(json.dumps({
    "normal": normal.tolist(),
    "depth_positive": bool(np.all(camera_depth > 0)),
    "normal_span": float(normal[1, 0] - normal[0, 0]),
    "telephoto_span": float(telephoto[1, 0] - telephoto[0, 0]),
    "rgb_size": list(Image.open(rgb).size),
    "depth_size": list(Image.open(depth).size),
    "depth_max": max(Image.open(depth).getdata()),
}))
"""
    completed = subprocess.run(
        [
            str(worker_python),
            "-c",
            script,
            project.model_dump_json(),
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["depth_positive"] is True
    assert result["normal"][0][0] < 256 < result["normal"][1][0]
    assert result["telephoto_span"] > result["normal_span"]
    assert result["rgb_size"] == [512, 512]
    assert result["depth_size"] == [512, 512]
    assert result["depth_max"] > 0


def test_geometry_worker_builds_selected_hair_and_garment_proxies():
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    project = Project()
    project.character.appearance.hair_proxy = "hair_proxy_soft_bob"
    project.character.appearance.garment_proxy = "garment_proxy_field_jacket"
    script = r"""
import json
import sys

import numpy as np
import trimesh

from services.geometry_worker.main import BODY_JOINT, _appearance_proxy_meshes

project = json.loads(sys.argv[1])
joints = np.zeros((22, 3), dtype=np.float32)
joints[BODY_JOINT["left_hip"]] = (-0.18, 0.95, 0)
joints[BODY_JOINT["right_hip"]] = (0.18, 0.95, 0)
joints[BODY_JOINT["neck"]] = (0, 1.62, 0)
joints[BODY_JOINT["head"]] = (0, 1.82, 0)
joints[BODY_JOINT["left_shoulder"]] = (-0.32, 1.56, 0)
joints[BODY_JOINT["right_shoulder"]] = (0.32, 1.56, 0)
hair, garment = _appearance_proxy_meshes(trimesh, project, joints)
print(json.dumps({
    "hair_vertices": int(len(hair.vertices)),
    "garment_vertices": int(len(garment.vertices)),
    "hair_height": float(hair.extents[1]),
    "garment_width": float(garment.extents[0]),
}))
"""
    completed = subprocess.run(
        [str(worker_python), "-c", script, project.model_dump_json()],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["hair_vertices"] > 100
    assert result["garment_vertices"] == 8
    assert result["hair_height"] > 0.4
    assert result["garment_width"] > 0.7


def test_head_alignment_preserves_gnm_front_facing_direction():
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    script = r"""
import json

import numpy as np

from services.geometry_worker.main import BODY_JOINT, _orthonormal_head_frame

joints = np.zeros((22, 3), dtype=np.float32)
joints[BODY_JOINT["neck"]] = (0, 1.6, 0)
joints[BODY_JOINT["head"]] = (0, 1.8, 0)
joints[BODY_JOINT["left_shoulder"]] = (0.3, 1.5, 0)
joints[BODY_JOINT["right_shoulder"]] = (-0.3, 1.5, 0)
_, rotation, _ = _orthonormal_head_frame(joints)
print(json.dumps({
    "local_left": rotation[:, 0].tolist(),
    "local_up": rotation[:, 1].tolist(),
    "local_front": rotation[:, 2].tolist(),
    "determinant": float(np.linalg.det(rotation)),
}))
"""
    completed = subprocess.run(
        [str(worker_python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["local_left"] == pytest.approx([1, 0, 0])
    assert result["local_up"] == pytest.approx([0, 1, 0])
    assert result["local_front"] == pytest.approx([0, 0, 1])
    assert result["determinant"] == pytest.approx(1)


def test_smplx_head_trim_does_not_delete_raised_limbs():
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    script = r"""
import json

import numpy as np

from services.geometry_worker.main import BODY_JOINT, _trim_smplx_head

joints = np.zeros((22, 3), dtype=np.float32)
joints[BODY_JOINT["neck"]] = (0, 1.5, 0)
joints[BODY_JOINT["head"]] = (0, 1.7, 0)
vertices = np.asarray([
    [-0.05, 1.67, 0], [0.05, 1.67, 0], [0, 1.8, 0],
    [0.8, 1.72, 0], [0.9, 1.72, 0], [0.85, 1.85, 0],
], dtype=np.float32)
faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
kept = _trim_smplx_head(vertices, faces, joints)
print(json.dumps({"kept": kept.tolist()}))
"""
    completed = subprocess.run(
        [str(worker_python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["kept"] == [[3, 4, 5]]


def test_body_feature_controls_modify_real_geometry_coordinates():
    worker_python = Path("data/geometry-venv/bin/python")
    if not worker_python.is_file():
        pytest.skip("isolated geometry environment is not installed")
    script = r"""
import json

import numpy as np

from services.geometry_worker.main import BODY_JOINT, _apply_body_features

joints = np.zeros((22, 3), dtype=np.float32)
joints[BODY_JOINT["left_hip"]] = (-0.2, 1.0, 0)
joints[BODY_JOINT["right_hip"]] = (0.2, 1.0, 0)
joints[BODY_JOINT["spine2"]] = (0, 1.35, 0)
joints[BODY_JOINT["neck"]] = (0, 1.7, 0)
joints[BODY_JOINT["left_shoulder"]] = (-0.3, 1.62, 0)
joints[BODY_JOINT["right_shoulder"]] = (0.3, 1.62, 0)
vertices = np.asarray([
    [-0.3, 1.62, 0.15],
    [0.3, 1.62, -0.15],
    [-0.15, 0.0, 0.1],
    [0.15, 0.0, -0.1],
], dtype=np.float32)
base_vertices, _ = _apply_body_features(
    {"height": 1, "build": 0.5, "shoulder_width": 0.5, "leg_length": 0.5},
    vertices,
    joints,
)
tuned_vertices, _ = _apply_body_features(
    {"height": 1.1, "build": 0.9, "shoulder_width": 0.9, "leg_length": 0.9},
    vertices,
    joints,
)
print(json.dumps({
    "base_shoulder_width": float(base_vertices[1, 0] - base_vertices[0, 0]),
    "tuned_shoulder_width": float(tuned_vertices[1, 0] - tuned_vertices[0, 0]),
    "base_floor": float(base_vertices[2:, 1].min()),
    "tuned_floor": float(tuned_vertices[2:, 1].min()),
    "base_depth": float(np.ptp(base_vertices[:, 2])),
    "tuned_depth": float(np.ptp(tuned_vertices[:, 2])),
}))
"""
    completed = subprocess.run(
        [str(worker_python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["tuned_shoulder_width"] > result["base_shoulder_width"]
    assert result["tuned_floor"] < result["base_floor"]
    assert result["tuned_depth"] > result["base_depth"]
