"""Read-only Memory access. Single entry: answer_question()."""

from __future__ import annotations

import sys
from pathlib import Path

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from research_memory.engine.chat import answer_question
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import ChatAnswer


class MemoryService:
    def __init__(self, repo: KnowledgeRepository | None = None) -> None:
        self.repo = repo or KnowledgeRepository()

    def ask(self, question: str) -> ChatAnswer:
        return answer_question(question.strip(), repo=self.repo)


def memory_engine_status() -> str:
    try:
        from research_memory.engine.chat import answer_question as _ask

        _ = _ask
    except Exception as exc:  # noqa: BLE001 — status line only
        return f"error ({type(exc).__name__}: {exc})"
    return "connected (answer_question)"
