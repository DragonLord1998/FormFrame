#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from services.local_controller.formframe.conditioning import (
    BENCHMARK_VARIANTS,
    build_comparison_matrix_manifest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a pending A-F live A100 comparison-matrix manifest for an exported FormFrame job."
    )
    parser.add_argument("--manifest", required=True, type=Path, help="Path to the exported job manifest.json.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination JSON path. Defaults to comparison-matrix.json next to the job manifest.",
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        default=list(BENCHMARK_VARIANTS),
        help="Variant labels to scaffold. Defaults to A B C D E F.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    matrix = build_comparison_matrix_manifest(job_manifest, variants=args.variants)
    output = args.output or args.manifest.with_name("comparison-matrix.json")
    output.write_text(json.dumps(matrix, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
