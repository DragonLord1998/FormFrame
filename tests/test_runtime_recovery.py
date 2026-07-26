from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bridge.colab_cli import ColabCliError
from services.local_controller.formframe.models import RenderJob, RuntimeSnapshot
from services.local_controller.formframe.remote import RemoteRuntimeError
from services.local_controller.formframe.runtime import RuntimeManager


class FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.saved = []

    def save_job(self, job) -> None:
        self.saved.append(job.stage)


class FakeBroker:
    def __init__(self) -> None:
        self.payloads = []

    async def broadcast(self, payload) -> None:
        self.payloads.append(payload)


class RecoveringRemote:
    def __init__(self, *, fail_twice: bool = False) -> None:
        self.render_calls = 0
        self.start_calls = 0
        self.fail_twice = fail_twice

    def render(self, *_args):
        self.render_calls += 1
        if self.render_calls == 1 or self.fail_twice:
            raise ColabCliError("session unavailable")
        return "rendered"

    def start(self, progress):
        self.start_calls += 1
        progress("provisioning", "Reconnecting A100", 35, "Restoring pinned runtime")
        return {"gpu": "A100"}

    def stop(self):
        self.stop_calls = getattr(self, "stop_calls", 0) + 1


def runtime_with(remote, tmp_path: Path) -> RuntimeManager:
    runtime = object.__new__(RuntimeManager)
    runtime._remote = remote
    runtime._production_geometry = object()
    runtime._start_task = None
    runtime.store = FakeStore(tmp_path)
    runtime.settings = type(
        "Settings",
        (),
        {
            "colab_session": "formframe-a100",
            "remote_readiness_errors": lambda self: [],
        },
    )()
    runtime.jobs = {}
    runtime.broker = FakeBroker()
    runtime.snapshot = RuntimeSnapshot(
        status="rendering",
        label="Rendering",
        provider="colab",
        gpu="A100",
        readiness_errors=[],
    )
    return runtime


def test_colab_cli_failure_rehydrates_and_retries_once(tmp_path: Path):
    remote = RecoveringRemote()
    runtime = runtime_with(remote, tmp_path)
    job = RenderJob(project_id="project_test", provider="colab", status="rendering")
    result = asyncio.run(
        runtime._render_remote_with_recovery(
            job,
            tmp_path / "job.ffjob",
            tmp_path / "job",
            lambda *_args: None,
        )
    )
    assert result == "rendered"
    assert remote.start_calls == 1
    assert remote.render_calls == 2
    assert "Recovering A100 runtime" in runtime.store.saved
    assert runtime.snapshot.status == "rendering"


def test_runtime_recovery_does_not_loop(tmp_path: Path):
    remote = RecoveringRemote(fail_twice=True)
    runtime = runtime_with(remote, tmp_path)
    job = RenderJob(project_id="project_test", provider="colab", status="rendering")
    with pytest.raises(ColabCliError):
        asyncio.run(
            runtime._render_remote_with_recovery(
                job,
                tmp_path / "job.ffjob",
                tmp_path / "job",
                lambda *_args: None,
            )
        )
    assert remote.start_calls == 1
    assert remote.render_calls == 2


def test_integrity_failure_is_not_retried(tmp_path: Path):
    class IntegrityRemote(RecoveringRemote):
        def render(self, *_args):
            self.render_calls += 1
            raise RemoteRuntimeError("Downloaded result failed integrity verification")

    remote = IntegrityRemote()
    runtime = runtime_with(remote, tmp_path)
    job = RenderJob(project_id="project_test", provider="colab", status="rendering")
    with pytest.raises(RemoteRuntimeError):
        asyncio.run(
            runtime._render_remote_with_recovery(
                job,
                tmp_path / "job.ffjob",
                tmp_path / "job",
                lambda *_args: None,
            )
        )
    assert remote.start_calls == 0
    assert remote.render_calls == 1


def test_runtime_stop_stops_exact_remote_and_resets_runtime(tmp_path: Path):
    remote = RecoveringRemote()
    runtime = runtime_with(remote, tmp_path)
    runtime.snapshot.status = "ready"

    snapshot = asyncio.run(runtime.stop())

    assert remote.stop_calls == 1
    assert runtime._remote is None
    assert runtime._production_geometry is None
    assert snapshot.status == "offline"
    assert snapshot.label == "A100 stopped"
    assert "formframe-a100" in snapshot.detail
    assert runtime.broker.payloads[-1]["runtime"]["status"] == "offline"


def test_runtime_stop_rejects_active_render_job(tmp_path: Path):
    remote = RecoveringRemote()
    runtime = runtime_with(remote, tmp_path)
    runtime.jobs = {
        "job_active": RenderJob(
            project_id="project_test",
            provider="colab",
            status="rendering",
        )
    }

    with pytest.raises(RuntimeError, match="render job is active"):
        asyncio.run(runtime.stop())

    assert not hasattr(remote, "stop_calls")
