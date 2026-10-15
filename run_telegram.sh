#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

if [[ -x "${ROOT}/.venv312/bin/python" ]]; then
  PYTHON="${ROOT}/.venv312/bin/python"
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.12)"
else
  PYTHON="$(command -v python3)"
fi

MODE="${1:-}"

if ! "$PYTHON" -c "import dotenv, telegram" >/dev/null 2>&1; then
  echo "telegram bot deps missing. install with:" >&2
  echo "  $PYTHON -m pip install -r ${ROOT}/telegram_memory/requirements.txt" >&2
  exit 1
fi

cd "${ROOT}/telegram_memory"
if [[ "${MODE}" == "check" ]]; then
  exec "$PYTHON" app.py check
fi

if [[ "${MODE}" == "user" ]]; then
  echo "Telethon user mode was removed. Use ./run_telegram.sh (Bot API)." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "missing ${ROOT}/telegram_memory/.env" >&2
  echo "copy .env.example to .env and fill TELEGRAM_BOT_TOKEN" >&2
  echo "(allow users via Streamlit Telegram admin page)" >&2
  exit 1
fi

exec "$PYTHON" app.py "$@"
