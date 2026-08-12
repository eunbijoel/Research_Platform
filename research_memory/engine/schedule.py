"""Project schedule / calendar helpers (MVP)."""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from research_memory.kb.repository import KnowledgeRepository

EVENT_TYPES = ("meeting", "submission", "task", "milestone")
STATUSES = ("planned", "in_progress", "done")

EVENT_TYPE_LABELS = {
    "meeting": "회의",
    "submission": "제출",
    "task": "작업",
    "milestone": "마일스톤",
}
STATUS_LABELS = {
    "planned": "예정",
    "in_progress": "진행중",
    "done": "완료",
}
EVENT_TYPE_MARK = {
    "meeting": "🗓",
    "submission": "📤",
    "task": "☑",
    "milestone": "🏁",
}


def normalize_event_type(value: str | None) -> str:
    raw = (value or "").strip().lower()
    return raw if raw in EVENT_TYPES else "task"


def normalize_status(value: str | None) -> str:
    raw = (value or "").strip().lower()
    aliases = {
        "예정": "planned",
        "진행중": "in_progress",
        "진행": "in_progress",
        "완료": "done",
    }
    raw = aliases.get(raw, raw)
    return raw if raw in STATUSES else "planned"


def event_type_label(value: str | None) -> str:
    return EVENT_TYPE_LABELS.get(normalize_event_type(value), "작업")


def status_label(value: str | None) -> str:
    return STATUS_LABELS.get(normalize_status(value), "예정")


def month_bounds(year: int, month: int) -> tuple[str, str]:
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def list_month_items(
    year: int,
    month: int,
    *,
    repo: KnowledgeRepository | None = None,
    project_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    repo = repo or KnowledgeRepository()
    date_from, date_to = month_bounds(year, month)
    return repo.list_schedule_items(
        project_id=project_id or None,
        status=status or None,
        date_from=date_from,
        date_to=date_to,
    )


def items_by_date(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = str(item.get("date") or "")
        if key:
            grouped[key].append(item)
    return dict(grouped)


def month_grid(year: int, month: int) -> list[list[date | None]]:
    """Return weeks as lists of 7 dates (Mon–Sun). Empty cells are None."""
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    grid: list[list[date | None]] = []
    for week in weeks:
        row: list[date | None] = []
        for day in week:
            row.append(day if day.month == month else None)
        grid.append(row)
    return grid


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def parse_iso_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
