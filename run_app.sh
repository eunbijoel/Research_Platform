#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)${PYTHONPATH:+:$PYTHONPATH}"
export TMPDIR="${TMPDIR:-/mnt/data/eunbi/tmp}"
mkdir -p "$TMPDIR"

if [[ -x ./.venv312/bin/streamlit ]]; then
  STREAMLIT=./.venv312/bin/streamlit
elif [[ -x ./.venv/bin/streamlit ]]; then
  STREAMLIT=./.venv/bin/streamlit
elif command -v streamlit >/dev/null 2>&1; then
  STREAMLIT=streamlit
else
  echo "streamlit not found. Run: ./scripts/setup_env.sh" >&2
  exit 1
fi

PORT="${PORT:-8505}"
exec "$STREAMLIT" run app.py --server.port "$PORT" --server.address 127.0.0.1
