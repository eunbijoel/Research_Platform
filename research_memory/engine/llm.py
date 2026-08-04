from __future__ import annotations

import json
import urllib.error
import urllib.request

from research_memory.config import (
    LLM_TIMEOUT_SEC,
    MOCK_LLM,
    MODEL_NAME,
    OLLAMA_URL,
)


class LLMConnectionError(Exception):
    pass


_active_model: str | None = None


def get_active_model() -> str:
    return (_active_model or MODEL_NAME).strip() or MODEL_NAME


def set_active_model(name: str) -> None:
    global _active_model
    name = (name or "").strip()
    _active_model = name or None


def list_ollama_models() -> list[str]:
    """Return installed Ollama model names (chat-capable preferred order)."""
    if MOCK_LLM:
        return []
    try:
        tags_url = OLLAMA_URL.replace("/api/generate", "/api/tags")
        with urllib.request.urlopen(tags_url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return []
    names = []
    for m in body.get("models") or []:
        name = (m.get("name") or "").strip()
        if name:
            names.append(name)
    # Keep default first when present; push obvious embed models lower
    embed_like = {"nomic-embed-text", "mxbai-embed-large", "all-minilm", "bge-m3"}
    chat = [n for n in names if n.split(":")[0] not in embed_like]
    embed = [n for n in names if n.split(":")[0] in embed_like]
    ordered = chat + embed
    if MODEL_NAME in ordered:
        ordered.remove(MODEL_NAME)
        ordered.insert(0, MODEL_NAME)
    return ordered


def generate_text(prompt: str, *, model: str | None = None) -> str:
    if MOCK_LLM:
        return (
            "MOCK_LLM=true 상태입니다. 아래 근거를 바탕으로 한 추출형 답변을 사용하세요.\n"
            "실제 답변을 원하면 RM_MOCK_LLM=false 와 Ollama를 연결하세요."
        )

    model_name = (model or get_active_model()).strip() or MODEL_NAME
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=LLM_TIMEOUT_SEC) as response:
            body = json.loads(response.read().decode("utf-8"))
            return (body.get("response") or "").strip()
    except urllib.error.URLError as exc:
        raise LLMConnectionError(
            f"Cannot reach Ollama at {OLLAMA_URL} (model={model_name}): {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise LLMConnectionError(f"LLM call failed: {exc}") from exc


def llm_available() -> bool:
    if MOCK_LLM:
        return False
    try:
        tags_url = OLLAMA_URL.replace("/api/generate", "/api/tags")
        with urllib.request.urlopen(tags_url, timeout=2) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False
