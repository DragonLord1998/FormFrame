#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
gnm_revision="8294570e208cf55c4710d13c1c269d523524f591"
gnm_dir="$repo_dir/data/models/gnm"
smplx_dir="$repo_dir/data/models/smplx"
venv_dir="$repo_dir/data/geometry-venv"
source_dir="${1:-}"

find_python() {
  for candidate in "${FORMFRAME_GEOMETRY_BOOTSTRAP_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3; do
    [[ -n "$candidate" ]] || continue
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
        command -v "$candidate"
        return
      fi
    elif [[ -x "$candidate" ]]; then
      if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
        printf '%s\n' "$candidate"
        return
      fi
    fi
  done
  return 1
}

geometry_python="$(find_python || true)"
if [[ -z "$geometry_python" ]]; then
  echo "Python 3.10 or newer is required for the isolated geometry worker." >&2
  exit 1
fi

mkdir -p "$repo_dir/data/models"
if [[ ! -d "$gnm_dir/.git" ]]; then
  git clone https://github.com/google/gnm.git "$gnm_dir"
fi
git -C "$gnm_dir" fetch --depth 1 origin "$gnm_revision"
git -C "$gnm_dir" checkout --detach "$gnm_revision"

gnm_asset="$gnm_dir/gnm/shape/data/versions/v3_0/gnm_head.npz"
if [[ ! -f "$gnm_asset" || "$(wc -c < "$gnm_asset")" -lt 10000000 ]]; then
  echo "Pinned GNM head asset is incomplete." >&2
  exit 1
fi

if [[ ! -d "$venv_dir" ]]; then
  "$geometry_python" -m venv "$venv_dir"
fi
"$venv_dir/bin/python" -m pip install --upgrade pip wheel
"$venv_dir/bin/python" -m pip install -e "$gnm_dir/gnm/shape[pytorch]" smplx trimesh

mkdir -p "$smplx_dir"
if [[ -n "$source_dir" ]]; then
  if [[ ! -d "$source_dir" ]]; then
    echo "SMPL-X source directory does not exist: $source_dir" >&2
    exit 1
  fi
  find "$source_dir" -maxdepth 1 -type f \
    \( -name 'SMPLX_*.npz' -o -name 'SMPLX_*.pkl' \) \
    -exec cp -p {} "$smplx_dir/" \;
fi

if [[ ! -f "$smplx_dir/SMPLX_NEUTRAL.npz" && ! -f "$smplx_dir/SMPLX_NEUTRAL.pkl" ]]; then
  echo "GNM and the geometry environment are ready."
  echo "Download SMPL-X after signing in, then rerun:"
  echo "  ./scripts/install-geometry.sh /path/to/downloaded/smplx/models/smplx"
  exit 2
fi

echo "Real GNM + SMPL-X geometry is ready."
echo "Set:"
echo "  FORMFRAME_GNM_CHECKOUT=$gnm_dir"
echo "  FORMFRAME_SMPLX_MODEL_DIR=$smplx_dir"
echo "  FORMFRAME_GEOMETRY_PYTHON=$venv_dir/bin/python"

