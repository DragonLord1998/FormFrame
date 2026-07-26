from __future__ import annotations

import argparse
import json
from pathlib import Path

from formframe_gateway.bundle import validate_bundle
from formframe_gateway.comfy import ComfyClient
from formframe_gateway.settings import GatewaySettings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--bundle", required=True)
    args = parser.parse_args()

    settings = GatewaySettings.from_environment()
    bundle = Path(args.bundle).resolve()
    validate_bundle(bundle, args.job_id)
    comfy = ComfyClient(
        settings.comfy_url,
        settings.root / "workflows" / "controlled-character-v1.api.json",
    )
    prompt_id = comfy.submit(bundle)
    comfy.wait(prompt_id)
    output_dir = settings.outbox / args.job_id
    required = [output_dir / "result.png", output_dir / "preview.webp", output_dir / "result.json"]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing remote outputs: {missing}")
    print(
        "FORMFRAME_CLI_RESULT:"
        + json.dumps(
            {
                "job_id": args.job_id,
                "prompt_id": prompt_id,
                "result_path": str(required[0]),
                "preview_path": str(required[1]),
                "manifest_path": str(required[2]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

