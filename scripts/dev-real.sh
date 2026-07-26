#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

if [[ ! -f .env ]]; then
  echo "Copy .env.example to .env and configure the private runtime values first." >&2
  exit 1
fi

set -a
source .env
set +a

exec ./scripts/dev.sh

