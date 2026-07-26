#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

.venv/bin/python -m pytest -q
npm run build --prefix apps/mac-ui
