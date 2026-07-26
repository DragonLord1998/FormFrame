from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, base_url: str, workflow_path: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.workflow_path = workflow_path
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
