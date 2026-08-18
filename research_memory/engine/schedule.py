"""Project schedule / calendar helpers (MVP)."""
from __future__ import annotations

import calendar
import re
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
EVENT_CHIP_CLASS = {
    "meeting": "rm-chip-meeting",
    "submission": "rm-chip-submission",
    "task": "rm-chip-task",
    "milestone": "rm-chip-milestone",
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


def event_chip_class(event_type: str | None, status: str | None = None) -> str:
    if normalize_status(status) == "done":
        return "rm-chip-done"
    return EVENT_CHIP_CLASS.get(normalize_event_type(event_type), "rm-chip-task")


_TIME_PREFIX = re.compile(r"^(\d{1,2}:\d{2})\s*(.*)$")


def chip_time_and_title(title: str | None, note: str | None = None) -> tuple[str | None, str]:
    """Split optional HH:MM prefix from title or note for calendar chip display."""
    raw_title = (title or "").strip() or "(제목 없음)"
    match = _TIME_PREFIX.match(raw_title)
    if match:
        rest = (match.group(2) or "").strip()
        return match.group(1), rest or raw_title
    note_text = (note or "").strip()
    note_match = re.match(r"^(\d{1,2}:\d{2})\b", note_text)
    if note_match:
        return note_match.group(1), raw_title
    return None, raw_title


def render_calendar_html(
    *,
    year: int,
    month: int,
    grid: list[list[date]],
    by_day: dict[str, list[dict[str, Any]]],
    today: date,
    selected_date: str | None = None,
    selected_item_id: str | None = None,
    max_chips: int = 3,
) -> str:
    """Render myown-style month grid as HTML (Sun start, 6 weeks)."""
    import html as html_mod

    weekday_labels = ["일", "월", "화", "수", "목", "금", "토"]
    parts: list[str] = ['<div class="rm-cal-html">', '<div class="rm-cal-html-head">']
    for label in weekday_labels:
        parts.append(f'<div class="rm-cal-html-wd">{label}</div>')
    parts.append('</div><div class="rm-cal-html-body">')

    for week in grid:
        for day in week:
            date_key = day.isoformat()
            in_month = day.month == month
            day_items = by_day.get(date_key, [])
            preview_items = day_items[:max_chips]
            cell_classes = ["rm-cal-html-cell"]
            if not in_month:
                cell_classes.append("out")
            if day == today:
                cell_classes.append("today")
            if selected_date == date_key and not selected_item_id:
                cell_classes.append("selected")
            if selected_item_id and any(it["id"] == selected_item_id for it in day_items):
                cell_classes.append("selected")

            parts.append(f'<div class="{" ".join(cell_classes)}">')
            parts.append(
                f'<a class="rm-cal-html-daynum" href="?sched_date={html_mod.escape(date_key)}">{day.day}</a>'
            )
            parts.append(
                f'<a class="rm-cal-html-hit" href="?sched_date={html_mod.escape(date_key)}" '
                f'aria-label="{html_mod.escape(date_key)} 일정 추가"></a>'
            )
            parts.append('<div class="rm-cal-html-chips">')
            for it in preview_items:
                iid = html_mod.escape(str(it["id"]))
                chip_time, chip_title = chip_time_and_title(
                    str(it.get("title") or ""),
                    str(it.get("note") or ""),
                )
                chip_title_esc = html_mod.escape(chip_title)
                chip_cls = event_chip_class(it.get("event_type"), it.get("status"))
                if selected_item_id == it["id"]:
                    chip_cls += " selected"
                time_html = ""
                if chip_time:
                    time_html = (
                        f'<span class="rm-cal-html-chip-time">'
                        f'{html_mod.escape(chip_time)}</span>'
                    )
                tooltip = html_mod.escape(
                    f"{chip_time} {chip_title}".strip() if chip_time else chip_title
                )
                parts.append(
                    f'<a class="rm-cal-html-chip {chip_cls}" '
                    f'href="?sched_item={iid}&sched_date={html_mod.escape(date_key)}" '
                    f'title="{tooltip}">{time_html}'
                    f'<span class="rm-cal-html-chip-title">{chip_title_esc}</span></a>'
                )
            parts.append("</div>")
            if len(day_items) > max_chips:
                parts.append(
                    f'<div class="rm-cal-html-more">+{len(day_items) - max_chips}</div>'
                )
            parts.append("</div>")

    parts.append("</div></div>")
    return "".join(parts)


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


def calendar_grid_sunday(year: int, month: int) -> list[list[date]]:
    """6-week Sun–Sat grid (myown-style), including adjacent month days."""
    from datetime import timedelta

    first = date(year, month, 1)
    offset = (first.weekday() + 1) % 7
    start = first - timedelta(days=offset)
    grid: list[list[date]] = []
    cursor = start
    for _ in range(6):
        week: list[date] = []
        for _ in range(7):
            week.append(cursor)
            cursor += timedelta(days=1)
        grid.append(week)
    return grid


def grid_date_bounds(grid: list[list[date]]) -> tuple[str, str]:
    flat = [day for week in grid for day in week]
    return flat[0].isoformat(), flat[-1].isoformat()


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
