"""Read-only Memory access for Research Memory Bot."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from research_memory.engine.chat import answer_question
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import ChatAnswer


@dataclass(frozen=True)
class ScheduleQuery:
    label: str
    date_from: str
    date_to: str
    items: list[dict[str, Any]]


def week_bounds(day: date | None = None) -> tuple[date, date]:
    """Monday–Sunday containing ``day`` (default: today)."""
    day = day or date.today()
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return start, end


class MemoryService:
    def __init__(self, repo: KnowledgeRepository | None = None) -> None:
        self.repo = repo or KnowledgeRepository()

    def ask(self, question: str) -> ChatAnswer:
        return answer_question(question.strip(), repo=self.repo)

    def list_today(self, day: date | None = None) -> ScheduleQuery:
        day = day or date.today()
        day_s = day.isoformat()
        items = self.repo.list_schedule_items(date_from=day_s, date_to=day_s)
        return ScheduleQuery(
            label=f"오늘 ({day_s})",
            date_from=day_s,
            date_to=day_s,
            items=items,
        )

    def list_week(self, day: date | None = None) -> ScheduleQuery:
        start, end = week_bounds(day)
        items = self.repo.list_schedule_items(
            date_from=start.isoformat(),
            date_to=end.isoformat(),
        )
        return ScheduleQuery(
            label=f"이번 주 ({start.isoformat()} ~ {end.isoformat()})",
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            items=items,
        )


def memory_engine_status() -> str:
    try:
        from research_memory.engine.chat import answer_question as _ask

        _ = _ask
    except Exception as exc:  # noqa: BLE001 — status line only
        return f"error ({type(exc).__name__}: {exc})"
    return "connected (answer_question)"
