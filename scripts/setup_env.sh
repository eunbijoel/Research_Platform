#!/usr/bin/env bash
# Research Memory + Coding Agent — Python 3.12 venv on /mnt/data (avoids full root disk).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export TMPDIR="${TMPDIR:-/mnt/data/eunbi/tmp}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/mnt/data/eunbi/pip-cache}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-/mnt/data/eunbi/cache}"
mkdir -p "$TMPDIR" "$PIP_CACHE_DIR" "$XDG_CACHE_HOME"

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "python3.12 not found. Install Python 3.12+ (deepagents-code requires it)." >&2
  exit 1
fi

VENV="${VENV:-$ROOT/.venv312}"
echo "Creating venv: $VENV (Python $(python3.12 -V))"
echo "Using TMPDIR=$TMPDIR (root disk may be full — do not use system pip on 3.10)"
python3.12 -m venv "$VENV"
"$VENV/bin/pip" install -U pip wheel
"$VENV/bin/pip" install -r requirements.txt

echo ""
echo "Done. Run the app with:"
echo "  ./run_app.sh"
echo ""
echo "Or activate manually:"
echo "  source $VENV/bin/activate"
