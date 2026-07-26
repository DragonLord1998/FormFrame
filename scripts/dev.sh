#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if [[ ! -x .venv/bin/uvicorn || ! -d apps/mac-ui/node_modules ]]; then
  echo "Dependencies are missing. Run ./scripts/setup.sh first."
  exit 1
fi

cleanup() {
  if [[ -n "${controller_pid:-}" ]]; then
    kill "$controller_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

.venv/bin/uvicorn services.local_controller.formframe.app:app --host 127.0.0.1 --port 8000 --reload &
controller_pid=$!

echo "FormFrame Studio: http://127.0.0.1:7860"
npm run dev --prefix apps/mac-ui
