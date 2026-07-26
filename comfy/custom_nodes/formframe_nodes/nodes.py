from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import numpy as np
import torch
from PIL import Image, PngImagePlugin

REMOTE_ROOT = Path(os.environ.get("FORMFRAME_REMOTE_ROOT", "/content/formframe")).resolve()
INBOX = REMOTE_ROOT / "inbox"
OUTBOX = REMOTE_ROOT / "outbox"
REQUIRED = {"manifest.json", "rgb.webp", "depth.png", "pose.png"}


def _image(data: bytes) -> torch.Tensor:
    import io

    value = Image.open(io.BytesIO(data)).convert("RGB")
    return torch.from_numpy(np.asarray(value).astype(np.float32) / 255.0)[None, ...]


def _bundle(value: str) -> Path:
    path = Path(value).resolve()
    if path.parent != INBOX or path.suffix != ".ffjob":
        raise ValueError("FormFrame bundle path is outside the private inbox")
    return path


class FormFrameJobLoader:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"bundle_path": ("STRING", {"default": ""})}}

    RETURN_TYPES = (
        "IMAGE",
        "IMAGE",
        "IMAGE",
        "STRING_PROMPT",
        "STRING_PROMPT",
        "INT",
        "INT",
        "INT",
        "FLOAT",
        "FLOAT",
        "IMAGE",
        "STRING",
    )
    RETURN_NAMES = (
        "rgb",
        "depth",
        "pose",
        "prompt",
        "negative_prompt",
        "seed",
        "width",
        "height",
        "depth_strength",
        "pose_strength",
        "inpaint_mask",
        "job_metadata",
    )
    FUNCTION = "load"
    CATEGORY = "FormFrame"

    def load(self, bundle_path: str):
        path = _bundle(bundle_path)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not REQUIRED.issubset(names) or any(Path(name).name != name for name in names):
                raise ValueError("FormFrame bundle layout is invalid")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("workflow") != "controlled-character-v1":
                raise ValueError("Unsupported FormFrame workflow")
            for name, key in (("rgb.webp", "rgb"), ("depth.png", "depth"), ("pose.png", "pose")):
                digest = hashlib.sha256(archive.read(name)).hexdigest()
                if digest != manifest["assets"][key]["sha256"]:
                    raise ValueError(f"{key} conditioning hash mismatch")
            for reference in manifest["assets"].get("references", []):
                name = str(reference.get("path", ""))
                if (
                    not name.startswith("ref_")
                    or not name.endswith(".webp")
                    or Path(name).name != name
                    or name not in names
                ):
                    raise ValueError("Reference asset path is invalid")
                digest = hashlib.sha256(archive.read(name)).hexdigest()
                if digest != reference.get("sha256"):
                    raise ValueError("Reference asset hash mismatch")
            controls = manifest["controls"]
            # VideoX-Fun expects an IMAGE mask and binarizes it internally.
            # An all-white mask regenerates the full frame while the RGB guide
            # and structural controls preserve composition.
            inpaint_mask = torch.ones(
                (1, int(manifest["height"]), int(manifest["width"]), 3),
                dtype=torch.float32,
            )
            return (
                _image(archive.read("rgb.webp")),
                _image(archive.read("depth.png")),
                _image(archive.read("pose.png")),
                str(manifest["prompt"]),
                str(manifest["negative_prompt"]),
                int(manifest["seed"]),
                int(manifest["width"]),
                int(manifest["height"]),
                float(controls["depth_strength"]),
                float(controls["pose_strength"]),
                inpaint_mask,
                json.dumps(manifest, sort_keys=True),
            )


class FormFrameResultSaver:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "job_metadata": ("STRING", {"forceInput": True}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("result_path",)
    FUNCTION = "save"
    CATEGORY = "FormFrame"
    OUTPUT_NODE = True

    def save(self, images: torch.Tensor, job_metadata: str):
        manifest = json.loads(job_metadata)
        job_id = str(manifest["job_id"])
        if not job_id.startswith("job_") or len(job_id) != 16:
            raise ValueError("FormFrame job ID is invalid")
        output_dir = (OUTBOX / job_id).resolve()
        if output_dir.parent != OUTBOX:
            raise ValueError("FormFrame output path is invalid")
        output_dir.mkdir(parents=True, exist_ok=True)
        array = images[0].detach().cpu().numpy()
        image = Image.fromarray(np.clip(array * 255.0, 0, 255).astype(np.uint8), "RGB")
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("formframe", json.dumps(manifest, sort_keys=True))
        result = output_dir / "result.png"
        image.save(result, pnginfo=png_info, optimize=True)
        preview = image.copy()
        preview.thumbnail((640, 640))
        preview.save(output_dir / "preview.webp", format="WEBP", quality=82, method=4)
        input_hashes = {
            key: value["sha256"]
            for key, value in manifest["assets"].items()
            if isinstance(value, dict) and "sha256" in value
        }
        for reference in manifest["assets"].get("references", []):
            input_hashes[f"reference:{reference['role']}"] = reference["sha256"]
        result_document = {
            "schema_version": 1,
            "job_id": job_id,
            "workflow": manifest["workflow"],
            "workflow_hash": manifest["workflow_hash"],
            "model": "Tongyi-MAI/Z-Image-Turbo",
            "model_revision": manifest["versions"]["z_image_turbo"],
            "control_model": "alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1-2602-8steps",
            "control_model_revision": manifest["versions"]["z_image_controlnet"],
            "control_model_sha256": manifest["versions"]["z_image_controlnet_sha256"],
            "sampler": "ZImageControlSampler",
            "scheduler": "Flow",
            "steps": 8,
            "cfg": 0,
            "seed": manifest["seed"],
            "identity_mode": manifest["controls"].get("identity_mode", "none"),
            "identity_lora_strength": manifest["controls"].get("identity_lora_strength", 0),
            "identity_trigger_token": manifest["controls"].get("identity_trigger_token", ""),
            "input_hashes": input_hashes,
            "output_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
        }
        (output_dir / "result.json").write_text(
            json.dumps(result_document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return (str(result),)
