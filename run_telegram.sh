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

if [[ "${MODE}" == "user" ]]; then
  if ! "$PYTHON" -c "import dotenv, telethon" >/dev/null 2>&1; then
    echo "telethon deps missing. install with:" >&2
    echo "  $PYTHON -m pip install -r ${ROOT}/telegram_memory/requirements.txt" >&2
    exit 1
  fi
  cd "${ROOT}/telegram_memory"
  if [[ ! -f .env ]]; then
    echo "missing ${ROOT}/telegram_memory/.env" >&2
    echo "set TELETHON_API_ID / TELETHON_API_HASH / TELETHON_ALLOWED_USER_IDS" >&2
    exit 1
  fi
  exec "$PYTHON" app.py user
fi

if ! "$PYTHON" -c "import dotenv, telegram" >/dev/null 2>&1; then
  echo "telegram bot deps missing. install with:" >&2
  echo "  $PYTHON -m pip install -r ${ROOT}/telegram_memory/requirements.txt" >&2
  exit 1
fi

cd "${ROOT}/telegram_memory"
if [[ "${MODE}" == "check" ]]; then
  exec "$PYTHON" app.py check
fi

if [[ ! -f .env ]]; then
  echo "missing ${ROOT}/telegram_memory/.env" >&2
  echo "copy .env.example to .env and fill TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_USER_IDS / TELEGRAM_ALLOWED_CHAT_IDS" >&2
  exit 1
fi

exec "$PYTHON" app.py "$@"
