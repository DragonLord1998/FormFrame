import json
import subprocess
from pathlib import Path

import pytest

from services.local_controller.formframe.geometry import ProceduralGuideGeometry, UnconfiguredModelGeometry
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
