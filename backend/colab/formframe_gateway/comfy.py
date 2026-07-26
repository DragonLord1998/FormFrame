from __future__ import annotations

import json
import hashlib
import re
import time
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


class ComfyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ComfyClient:
    def __init__(self, base_url: str, workflow_path: Path, lora_root: Path | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow_path = workflow_path
        self.lora_root = lora_root
        self.client = httpx.Client(base_url=self.base_url, timeout=60)

    def health(self) -> dict[str, Any]:
        response = self.client.get("/system_stats")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def interrupt(self) -> None:
        response = self.client.post("/interrupt")
        response.raise_for_status()

    def submit(self, bundle_path: Path) -> str:
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComfyError("Pinned ComfyUI workflow is missing or invalid") from exc
        if workflow.get("_formframe_workflow") != "controlled-character-v1":
            raise ComfyError("Pinned ComfyUI workflow identity is invalid")
        prompt = deepcopy(workflow["prompt"])
        loader = prompt.get("1")
        if not isinstance(loader, dict) or loader.get("class_type") != "FormFrameJobLoader":
            raise ComfyError("Pinned workflow loader node is invalid")
        loader["inputs"]["bundle_path"] = str(bundle_path)
        self._configure_identity_lora(prompt, bundle_path)
        response = self.client.post(
            "/prompt",
            json={"prompt": prompt, "client_id": f"formframe-{uuid4().hex}"},
        )
        response.raise_for_status()
        payload = response.json()
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyError("ComfyUI did not return a prompt ID")
        return prompt_id

    def _configure_identity_lora(self, prompt: dict[str, Any], bundle_path: Path) -> None:
        controlnet = prompt.get("3")
        lora_loader = prompt.get("7")
        if (
            not isinstance(controlnet, dict)
            or controlnet.get("class_type") != "LoadZImageControlNetInPipeline"
            or not isinstance(lora_loader, dict)
            or lora_loader.get("class_type") != "LoadZImageLora"
        ):
            raise ComfyError("Pinned workflow identity-LoRA nodes are invalid")
        try:
            with zipfile.ZipFile(bundle_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
        except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise ComfyError("FormFrame bundle manifest is unavailable") from exc
        if manifest.get("workflow_hash") != _sha256(self.workflow_path):
            raise ComfyError("FormFrame bundle workflow hash does not match the pinned workflow")
        identity_lora = manifest.get("assets", {}).get("identity_lora")
        if identity_lora is None:
            prompt.pop("7")
            controlnet["inputs"]["funmodels"] = ["2", 0]
            return
        if self.lora_root is None:
            raise ComfyError("Identity LoRA storage is not configured")
        name = identity_lora.get("path")
        digest = identity_lora.get("sha256")
        expected_bytes = identity_lora.get("bytes")
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"formframe_[a-f0-9]{64}\.safetensors", name)
            or not isinstance(digest, str)
            or name != f"formframe_{digest}.safetensors"
            or not isinstance(expected_bytes, int)
        ):
            raise ComfyError("Identity LoRA manifest metadata is invalid")
        root = self.lora_root.resolve()
        path = (root / name).resolve()
        if path.parent != root or not path.is_file() or path.stat().st_size != expected_bytes:
            raise ComfyError("Pinned identity LoRA is missing from ComfyUI")
        observed = _sha256(path)
        if observed != digest:
            raise ComfyError("Pinned identity LoRA failed its SHA-256 check")
        controls = manifest.get("controls", {})
        lora_loader["inputs"]["lora_name"] = name
        lora_loader["inputs"]["strength_model"] = float(
            controls.get("identity_lora_strength", 1)
        )

    def wait(
        self,
        prompt_id: str,
        *,
        timeout_seconds: float = 900,
        poll_seconds: float = 1,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = self.client.get(f"/history/{prompt_id}")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and prompt_id in payload:
                history = payload[prompt_id]
                status = history.get("status", {})
                if status.get("completed") is True:
                    return history
                messages = status.get("messages", [])
                if any(message and message[0] == "execution_error" for message in messages):
                    raise ComfyError("ComfyUI workflow execution failed")
            time.sleep(poll_seconds)
        raise ComfyError("Timed out waiting for ComfyUI")
