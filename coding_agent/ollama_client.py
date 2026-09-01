"""Ollama chat client with native tool calling."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from coding_agent.config import LLM_TIMEOUT_SEC, MODEL_NAME, OLLAMA_HOST


class OllamaError(Exception):
    pass


def list_models() -> list[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [m.get("name", "") for m in body.get("models") or [] if m.get("name")]


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call Ollama /api/chat. Returns the assistant message dict."""
    payload: dict[str, Any] = {
        "model": (model or MODEL_NAME).strip() or MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise OllamaError(f"Cannot reach Ollama at {OLLAMA_HOST}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise OllamaError(f"Ollama chat failed: {exc}") from exc

    message = body.get("message") or {}
    if not isinstance(message, dict):
        raise OllamaError(f"Unexpected Ollama response: {body!r}")
    return message


def available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False
