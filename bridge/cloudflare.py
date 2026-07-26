from __future__ import annotations

import time
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


class GatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    access_client_id: str = ""
    access_client_secret: str = ""
    development_token: str = ""
    timeout_seconds: float = 30

    def validate(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise GatewayError("Gateway URL must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise GatewayError("Remote FormFrame gateways must use HTTPS")
        has_access = bool(self.access_client_id and self.access_client_secret)
        if self.development_token and len(self.development_token) < 32:
            raise GatewayError(
                "Gateway development token must be at least 32 characters"
            )
        if not has_access and not self.development_token:
            raise GatewayError("Cloudflare Access credentials are not configured")


class CloudflareGateway:
    """Narrow client for the authenticated FormFrame gateway, never ComfyUI."""

    def __init__(self, config: GatewayConfig, transport: httpx.BaseTransport | None = None) -> None:
        config.validate()
        self.config = config
        headers = {"Accept": "application/json", "User-Agent": "FormFrame-Studio/1"}
        if config.access_client_id and config.access_client_secret:
            headers.update(
                {
                    "CF-Access-Client-Id": config.access_client_id,
                    "CF-Access-Client-Secret": config.access_client_secret,
                }
            )
        if config.development_token:
            headers["Authorization"] = f"Bearer {config.development_token}"
        self.client = httpx.Client(
            base_url=config.base_url,
            headers=headers,
            timeout=config.timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    def close(self) -> None:
        self.client.close()

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GatewayError(f"Gateway request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise GatewayError("Gateway returned a non-object response")
        return payload

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/v1/health")

    def check_assets(self, hashes: list[str]) -> list[str]:
        payload = self._json("POST", "/v1/assets/check", json={"hashes": hashes})
        missing = payload.get("missing")
        if not isinstance(missing, list) or any(not isinstance(value, str) for value in missing):
            raise GatewayError("Gateway asset-check response is invalid")
        return missing

    def submit(self, job_id: str, remote_bundle: str) -> dict[str, Any]:
        return self._json(
            "POST",
            "/v1/jobs",
            json={"job_id": job_id, "remote_bundle": remote_bundle},
        )

    def job(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/v1/jobs/{job_id}")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._json("POST", f"/v1/jobs/{job_id}/cancel")

    def wait(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 900,
        poll_seconds: float = 1,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            payload = self.job(job_id)
            status = payload.get("status")
            if status == "completed":
                return payload
            if status in {"failed", "cancelled"}:
                raise GatewayError(str(payload.get("error") or f"Remote job {status}"))
            time.sleep(poll_seconds)
        raise GatewayError("Timed out waiting for the remote render")

    def events(self, job_id: str):
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise GatewayError("The websockets package is required for live progress") from exc
        parsed = urlparse(self.config.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        url = f"{scheme}://{parsed.netloc}/v1/events/{job_id}"
        headers: dict[str, str] = {}
        if self.config.access_client_id and self.config.access_client_secret:
            headers["CF-Access-Client-Id"] = self.config.access_client_id
            headers["CF-Access-Client-Secret"] = self.config.access_client_secret
        if self.config.development_token:
            headers["Authorization"] = f"Bearer {self.config.development_token}"
        try:
            with connect(
                url,
                additional_headers=headers,
                open_timeout=self.config.timeout_seconds,
                close_timeout=5,
            ) as websocket:
                while True:
                    payload = json.loads(websocket.recv())
                    if not isinstance(payload, dict):
                        raise GatewayError("Gateway progress event is invalid")
                    yield payload
                    if payload.get("status") in {"completed", "failed", "cancelled"}:
                        return
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(f"Gateway WebSocket failed: {exc}") from exc

    def wait_live(
        self,
        job_id: str,
        *,
        on_event=None,
        timeout_seconds: float = 900,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            for payload in self.events(job_id):
                if on_event:
                    on_event(payload)
                if time.monotonic() - started > timeout_seconds:
                    raise GatewayError("Timed out waiting for remote progress")
                if payload.get("status") == "completed":
                    return payload
                if payload.get("status") in {"failed", "cancelled"}:
                    raise GatewayError(str(payload.get("error") or payload["status"]))
        except GatewayError:
            remaining = max(1, timeout_seconds - (time.monotonic() - started))
            return self.wait(job_id, timeout_seconds=remaining)
        raise GatewayError("Gateway progress channel closed before completion")

    def download_preview(self, job_id: str, destination: Path) -> None:
        try:
            response = self.client.get(f"/v1/jobs/{job_id}/preview")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"Preview download failed: {exc}") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)

    def benchmark(self, payload: bytes) -> dict[str, float]:
        started = time.monotonic()
        try:
            response = self.client.put(
                "/v1/benchmark",
                content=payload,
                headers={"Content-Type": "application/octet-stream"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayError(f"Gateway benchmark failed: {exc}") from exc
        elapsed = max(time.monotonic() - started, 1e-6)
        if response.content != payload:
            raise GatewayError("Gateway benchmark payload mismatch")
        return {
            "round_trip_ms": elapsed * 1000,
            "combined_mbps": (len(payload) * 2 * 8) / elapsed / 1_000_000,
        }
