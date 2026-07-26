from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.local_controller.formframe.config import FormFrameSettings
from services.local_controller.formframe.geometry import GnmSmplxGeometry
from services.local_controller.formframe.models import Project


def main() -> int:
    settings = FormFrameSettings.from_environment()
    geometry = GnmSmplxGeometry(
        settings,
        REPO_ROOT / "data" / "smoke-geometry",
        settings.geometry_python,
    )
    project = Project(name="GNM + SMPL-X smoke")
    project.render.width = 512
    project.render.height = 512
    result = geometry.evaluate(project)
    required = (
        "mesh_path",
        "conditioning_rgb_path",
        "conditioning_depth_path",
        "projected_joints",
    )
    for name in required:
        if not result.get(name):
            raise RuntimeError(f"Geometry smoke result is missing {name}")
    for name in ("mesh_path", "conditioning_rgb_path", "conditioning_depth_path"):
        path = Path(str(result[name]))
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Geometry smoke artifact is missing or empty: {path}")
    print(
        "FORMFRAME_GEOMETRY_SMOKE:"
        + json.dumps(
            {
                "provider": result["provider"],
                "counts": result["counts"],
                "mesh_path": result["mesh_path"],
                "conditioning_rgb_path": result["conditioning_rgb_path"],
                "conditioning_depth_path": result["conditioning_depth_path"],
                "projected_joint_count": len(result["projected_joints"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
