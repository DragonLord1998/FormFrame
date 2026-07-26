from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_FILES = {"manifest.json", "rgb.webp", "depth.png", "pose.png", "normal.png"}
REQUIRED_FILES = {"manifest.json", "rgb.webp", "depth.png", "pose.png"}
REFERENCE_FILE = re.compile(r"^ref_(face_front|face_left|face_right|outfit)_[a-f0-9]{12}\.webp$")
IDENTITY_LORA_FILE = re.compile(r"^formframe_([a-f0-9]{64})\.safetensors$")
TRIGGER_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
ALLOWED_MANIFEST_FIELDS = {
    "schema_version",
    "job_id",
    "workflow",
    "workflow_hash",
    "character_id",
    "project_id",
    "width",
    "height",
    "prompt",
    "negative_prompt",
    "seed",
    "denoise",
    "controls",
    "versions",
    "assets",
    "output",
    "provider",
}


@dataclass(frozen=True)
class ValidatedBundle:
    path: Path
    manifest: dict[str, Any]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_bundle(
    path: Path,
    expected_job_id: str,
    workflow_path: Path | None = None,
) -> ValidatedBundle:
    if not path.is_file() or path.suffix != ".ffjob":
        raise ValueError("Remote bundle is missing or has the wrong extension")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Remote bundle exceeds the 64 MB limit")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if any(Path(name).name != name for name in names):
                raise ValueError("Bundle paths must be flat file names")
            if not REQUIRED_FILES.issubset(names):
                raise ValueError(f"Bundle is missing {sorted(REQUIRED_FILES - names)}")
            unsupported = {
                name
                for name in names
                if name not in ALLOWED_FILES and not REFERENCE_FILE.fullmatch(name)
            }
            if unsupported:
                raise ValueError(f"Bundle contains unsupported files: {sorted(unsupported)}")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict):
                raise ValueError("Bundle manifest must be an object")
            unknown = set(manifest) - ALLOWED_MANIFEST_FIELDS
            if unknown:
                raise ValueError(f"Manifest contains unsupported fields: {sorted(unknown)}")
            if manifest.get("schema_version") != 1:
                raise ValueError("Unsupported conditioning-contract version")
            if manifest.get("job_id") != expected_job_id:
                raise ValueError("Bundle job ID does not match submission")
            if manifest.get("workflow") != "controlled-character-v1":
                raise ValueError("Only controlled-character-v1 is accepted")
            if not isinstance(manifest.get("workflow_hash"), str) or not re.fullmatch(
                r"[a-f0-9]{64}",
                manifest["workflow_hash"],
            ):
                raise ValueError("Manifest workflow hash is invalid")
            pinned_workflow = workflow_path or (
                Path(__file__).resolve().parents[3]
                / "comfy"
                / "workflows"
                / "controlled-character-v1.api.json"
            )
            if not pinned_workflow.is_file():
                raise ValueError("Pinned workflow is unavailable")
            if manifest["workflow_hash"] != _sha256_bytes(pinned_workflow.read_bytes()):
                raise ValueError("Manifest workflow hash does not match the pinned workflow")
            assets = manifest.get("assets")
            if not isinstance(assets, dict):
                raise ValueError("Manifest assets must be an object")
            for key in ("rgb", "depth", "pose"):
                entry = assets.get(key)
                if not isinstance(entry, dict) or entry.get("path") != {
                    "rgb": "rgb.webp",
                    "depth": "depth.png",
                    "pose": "pose.png",
                }[key]:
                    raise ValueError(f"Manifest {key} asset is invalid")
                data = archive.read(str(entry["path"]))
                if _sha256_bytes(data) != entry.get("sha256"):
                    raise ValueError(f"Manifest {key} hash mismatch")
            references = assets.get("references", [])
            if not isinstance(references, list) or len(references) > 4:
                raise ValueError("Manifest references must be a list of at most four assets")
            for reference in references:
                if not isinstance(reference, dict):
                    raise ValueError("Manifest reference entry is invalid")
                name = reference.get("path")
                if not isinstance(name, str) or not REFERENCE_FILE.fullmatch(name):
                    raise ValueError("Manifest reference path is invalid")
                if name not in names:
                    raise ValueError("Manifest reference file is missing")
                if _sha256_bytes(archive.read(name)) != reference.get("sha256"):
                    raise ValueError("Manifest reference hash mismatch")
            identity_lora = assets.get("identity_lora")
            controls = manifest.get("controls")
            if not isinstance(controls, dict):
                raise ValueError("Manifest controls must be an object")
            if identity_lora is not None:
                if not isinstance(identity_lora, dict):
                    raise ValueError("Manifest identity LoRA entry is invalid")
                name = identity_lora.get("path")
                match = IDENTITY_LORA_FILE.fullmatch(name) if isinstance(name, str) else None
                if (
                    match is None
                    or match.group(1) != identity_lora.get("sha256")
                    or not isinstance(identity_lora.get("bytes"), int)
                    or not 0 < identity_lora["bytes"] <= 2 * 1024 * 1024 * 1024
                ):
                    raise ValueError("Manifest identity LoRA metadata is invalid")
                strength = controls.get("identity_lora_strength")
                token = controls.get("identity_trigger_token")
                if (
                    controls.get("identity_mode") != "trained-lora"
                    or not isinstance(strength, (int, float))
                    or not -10 <= strength <= 10
                    or not isinstance(token, str)
                    or not TRIGGER_TOKEN.fullmatch(token)
                ):
                    raise ValueError("Manifest identity LoRA controls are invalid")
            elif controls.get("identity_mode") == "trained-lora":
                raise ValueError("Manifest trained identity mode requires a LoRA asset")
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("Bundle is corrupt") from exc
    return ValidatedBundle(path=path, manifest=manifest)
