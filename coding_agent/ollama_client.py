"""Ollama availability helpers for the coding agent UI."""

from __future__ import annotations

import json
import urllib.request


from coding_agent.config import OLLAMA_HOST


def list_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [m.get("name", "") for m in body.get("models") or [] if m.get("name")]


def available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False
