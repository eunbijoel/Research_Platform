#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/mnt/data/eunbi/tmp}"
mkdir -p "$TMPDIR"

if [[ -x ./.venv/bin/streamlit ]]; then
  STREAMLIT=./.venv/bin/streamlit
elif command -v streamlit >/dev/null 2>&1; then
  STREAMLIT=streamlit
else
  echo "streamlit not found. Create venv and: pip install -r requirements.txt" >&2
  exit 1
fi

PORT="${PORT:-8505}"
exec "$STREAMLIT" run app.py --server.port "$PORT" --server.address 127.0.0.1
