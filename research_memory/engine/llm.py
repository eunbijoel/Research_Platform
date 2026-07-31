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


def generate_text(prompt: str) -> str:
    if MOCK_LLM:
        return (
            "MOCK_LLM=true 상태입니다. 아래 근거를 바탕으로 한 추출형 답변을 사용하세요.\n"
            "실제 답변을 원하면 RM_MOCK_LLM=false 와 Ollama를 연결하세요."
        )

    payload = {
        "model": MODEL_NAME,
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
            f"Cannot reach Ollama at {OLLAMA_URL} (model={MODEL_NAME}): {exc}"
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
