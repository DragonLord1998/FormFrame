from __future__ import annotations

import math
import json
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Tuple

from .config import FormFrameSettings
from .models import Project

Point2D = Tuple[float, float]


class GeometryProvider(ABC):
    """Geometry seam implemented by the local guide now and GNM/body models later."""

    provider_id: str

    @abstractmethod
    def projected_joints(self, project: Project, width: int, height: int) -> Dict[str, Point2D]:
        """Return ground-truth guide joints projected through the active camera contract."""

    def conditioning_passes(self, project: Project) -> Dict[str, Path] | None:
        """Return authoritative RGB/depth passes when the provider renders real geometry."""
        return None


class ProceduralGuideGeometry(GeometryProvider):
    provider_id = "procedural-guide-v1"

    def projected_joints(self, project: Project, width: int, height: int) -> Dict[str, Point2D]:
        pose = project.pose
        center_x = width * (0.5 + pose.hip_shift * 0.06)
        top = height * 0.15
        scale = height * 0.58 * project.character.height
        shoulder = scale * (0.13 + project.character.shoulder_width * 0.05)
        hip = scale * 0.09
        torso = scale * 0.29
        leg = scale * (0.41 + project.character.leg_length * 0.07)
        arm = scale * 0.24

        def arm_point(side: int, angle: float, elbow: float) -> Tuple[Point2D, Point2D]:
            shoulder_point = (center_x + side * shoulder, top + torso * 0.34)
            upper_angle = math.radians(90 - side * angle)
            elbow_point = (
                shoulder_point[0] + side * math.cos(upper_angle) * arm,
                shoulder_point[1] + math.sin(upper_angle) * arm,
            )
            forearm_angle = upper_angle + side * math.radians(elbow)
            wrist_point = (
                elbow_point[0] + side * math.cos(forearm_angle) * arm * 0.88,
                elbow_point[1] + math.sin(forearm_angle) * arm * 0.88,
            )
            return elbow_point, wrist_point

        left_elbow, left_wrist = arm_point(-1, pose.left_arm, pose.left_elbow)
        right_elbow, right_wrist = arm_point(1, pose.right_arm, pose.right_elbow)
        hip_y = top + torso
        knee_y = hip_y + leg * 0.53
        return {
            "head": (center_x + pose.head_turn * 0.08, top),
            "neck": (center_x, top + scale * 0.09),
            "left_shoulder": (center_x - shoulder, top + torso * 0.34),
            "right_shoulder": (center_x + shoulder, top + torso * 0.34),
            "left_elbow": left_elbow,
            "right_elbow": right_elbow,
            "left_wrist": left_wrist,
            "right_wrist": right_wrist,
            "left_hip": (center_x - hip, hip_y),
            "right_hip": (center_x + hip, hip_y),
            "left_knee": (center_x - hip * 1.15, knee_y - pose.left_knee * 0.28),
            "right_knee": (center_x + hip * 1.2, knee_y - pose.right_knee * 0.28),
            "left_ankle": (center_x - hip * 1.25, hip_y + leg),
            "right_ankle": (center_x + hip * 1.32, hip_y + leg),
        }


class UnconfiguredModelGeometry(GeometryProvider):
    """Fail-closed placeholder for the licensed production geometry stack."""

    provider_id = "gnm-body-model-unconfigured"

    def projected_joints(self, project: Project, width: int, height: int) -> Dict[str, Point2D]:
        raise RuntimeError("GNM/body-model geometry assets and canonical alignment are not configured")


class GnmSmplxGeometry(GeometryProvider):
    """Calls the isolated real-model worker and caches its mesh/result artifacts."""

    provider_id = "gnm-v3-smplx"

    def __init__(
        self,
        settings: FormFrameSettings,
        cache_root: Path,
        python: Path | None = None,
    ) -> None:
        if not settings.smplx_assets_available:
            raise RuntimeError("Licensed SMPL-X model files are not configured")
        if not settings.gnm_assets_available:
            raise RuntimeError("A Git-LFS-complete GNM checkout is not configured")
        self.settings = settings
        self.cache_root = cache_root
        self.python = python or Path(sys.executable)
        self.worker = Path(__file__).resolve().parents[2] / "geometry_worker" / "main.py"

    def evaluate(self, project: Project) -> dict[str, object]:
        key = project.model_dump_json(exclude={"updated_at"})
        import hashlib

        digest = hashlib.sha256(key.encode()).hexdigest()[:20]
        output_dir = self.cache_root / digest
        result_path = output_dir / "geometry.json"
        if result_path.is_file():
            return json.loads(result_path.read_text(encoding="utf-8"))
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
        ) as handle:
            handle.write(project.model_dump_json())
            project_path = Path(handle.name)
        environment = dict(__import__("os").environ)
        environment["PYTHONPATH"] = str(self.settings.gnm_checkout)
        try:
            completed = subprocess.run(
                [
                    str(self.python),
                    str(self.worker),
                    "--project",
                    str(project_path),
                    "--smplx-model-dir",
                    str(self.settings.smplx_model_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                check=False,
                text=True,
                timeout=180,
                env=environment,
                shell=False,
            )
        finally:
            project_path.unlink(missing_ok=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr[-4000:] or "GNM/SMPL-X worker failed")
        if not result_path.is_file():
            raise RuntimeError("GNM/SMPL-X worker did not produce geometry.json")
        return json.loads(result_path.read_text(encoding="utf-8"))

    def projected_joints(self, project: Project, width: int, height: int) -> Dict[str, Point2D]:
        result = self.evaluate(project)
        values = result.get("projected_joints")
        if not isinstance(values, dict):
            raise RuntimeError("GNM/SMPL-X worker returned invalid projected joints")
        return {
            name: (float(point[0]), float(point[1]))
            for name, point in values.items()
            if isinstance(point, list) and len(point) == 2
        }

    def mesh_path(self, project: Project) -> Path:
        result = self.evaluate(project)
        path = Path(str(result.get("mesh_path", ""))).resolve()
        if not path.is_file() or self.cache_root.resolve() not in path.parents:
            raise RuntimeError("GNM/SMPL-X worker returned an invalid mesh path")
        return path

    def conditioning_passes(self, project: Project) -> Dict[str, Path]:
        result = self.evaluate(project)
        paths = {
            "rgb": Path(str(result.get("conditioning_rgb_path", ""))).resolve(),
            "depth": Path(str(result.get("conditioning_depth_path", ""))).resolve(),
        }
        cache_root = self.cache_root.resolve()
        for name, path in paths.items():
            if not path.is_file() or cache_root not in path.parents:
                raise RuntimeError(
                    f"GNM/SMPL-X worker returned an invalid {name} conditioning path"
                )
        return paths
