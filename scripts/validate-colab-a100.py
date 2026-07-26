#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from bridge.colab_cli import ColabCli, ColabCliConfig, require_a100
from services.local_controller.formframe.config import FormFrameSettings


def main() -> int:
    settings = FormFrameSettings.from_environment()
    if not settings.colab_cli_available or settings.colab_cli is None:
        raise RuntimeError(
            "FORMFRAME_COLAB_CLI does not point to the Google Colab CLI executable"
        )
    cli = ColabCli(
        ColabCliConfig(
            executable=settings.colab_cli,
            session_name=settings.colab_session,
            gpu=settings.colab_gpu,
            auth_provider=settings.colab_auth,
            config_path=settings.colab_config,
        )
    )
    created = False
    try:
        _result, created = cli.ensure_a100_session_with_ownership()
        if not created:
            raise RuntimeError(
                f"Refusing to take ownership of existing Colab session {settings.colab_session}"
            )
        probe = cli.probe()
        require_a100(probe)
        print(
            "FORMFRAME_A100_VALIDATION:"
            + json.dumps(
                {
                    "cuda_available": probe["cuda_available"],
                    "gpu": probe["gpu"],
                    "python": probe["python"],
                    "session": settings.colab_session,
                    "vram_bytes": probe["vram_bytes"],
                },
                sort_keys=True,
            )
        )
        return 0
    finally:
        if created:
            stopped = cli.stop()
            if stopped.returncode:
                raise RuntimeError(
                    stopped.stderr
                    or stopped.stdout
                    or f"Failed to stop Colab session {settings.colab_session}"
                )
            print(
                "FORMFRAME_A100_STOPPED:"
                + json.dumps({"session": settings.colab_session}, sort_keys=True)
            )


if __name__ == "__main__":
    raise SystemExit(main())
