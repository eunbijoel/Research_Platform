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

            parts.append(
                f'<div class="{" ".join(cell_classes)}" '
                f'data-sched-date="{html_mod.escape(date_key)}" '
                f'title="빈 곳을 클릭해서 일정 추가">'
            )
            parts.append(
                f'<button type="button" class="rm-cal-html-daynum" '
                f'data-sched-date="{html_mod.escape(date_key)}">{day.day}</button>'
            )
            parts.append(
                f'<button type="button" class="rm-cal-html-hit" '
                f'data-sched-date="{html_mod.escape(date_key)}" '
                f'aria-label="{html_mod.escape(date_key)} 일정 추가"></button>'
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
                    f'<button type="button" class="rm-cal-html-chip {chip_cls}" '
                    f'data-sched-date="{html_mod.escape(date_key)}" '
                    f'data-sched-item="{iid}" title="{tooltip}">{time_html}'
                    f'<span class="rm-cal-html-chip-title">{chip_title_esc}</span></button>'
                )
            parts.append("</div>")
            if len(day_items) > max_chips:
                parts.append(
                    f'<div class="rm-cal-html-more">+{len(day_items) - max_chips}</div>'
                )
            parts.append("</div>")

    parts.append("</div></div>")
    return "".join(parts)


CALENDAR_IFRAME_CSS = """
.rm-cal-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 0.75rem;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  padding: 1rem 1.1rem 1.15rem;
  font-family: "Source Sans 3", "Source Sans Pro", "Noto Sans KR", sans-serif;
  color: #0f172a;
}
.rm-cal-card button { font: inherit; }
.rm-cal-card-title {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 1.05rem;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 0.85rem;
}
.rm-cal-html { width: 100%; }
.rm-cal-html-head,
.rm-cal-html-body {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 0.5rem;
}
.rm-cal-html-wd {
  text-align: center;
  font-size: 0.75rem;
  font-weight: 600;
  color: #64748b;
  padding: 0.15rem 0 0.35rem;
}
.rm-cal-html-wd:first-child { color: #ef4444; }
.rm-cal-html-wd:last-child { color: #3b82f6; }
.rm-cal-html-body {
  min-height: 33rem;
  grid-auto-rows: minmax(7.25rem, 1fr);
}
.rm-cal-html-cell {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-height: 7.25rem;
  border: 1px solid #e2e8f0;
  border-radius: 0.5rem;
  background: #ffffff;
  padding: 0.45rem 0.5rem 0.35rem;
  overflow: hidden;
  user-select: none;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.rm-cal-html-cell:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.08);
  z-index: 2;
}
.rm-cal-html-cell.out {
  background: #f8fafc;
  border-color: transparent;
}
.rm-cal-html-cell.out .rm-cal-html-daynum { color: #94a3b8; }
.rm-cal-html-cell.today {
  border-color: rgba(37, 99, 235, 0.45);
  background: rgba(219, 234, 254, 0.45);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.18);
}
.rm-cal-html-cell.selected {
  border-color: #dbeafe;
  box-shadow: 0 0 0 2px rgba(219, 234, 254, 0.95);
}
.rm-cal-html-cell.today.selected {
  border-color: rgba(37, 99, 235, 0.45);
  background: rgba(219, 234, 254, 0.45);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.18), 0 0 0 4px rgba(219, 234, 254, 0.85);
}
.rm-cal-html-daynum,
.rm-cal-html-hit,
.rm-cal-html-chip {
  appearance: none;
  -webkit-appearance: none;
  border: 0;
  margin: 0;
  cursor: pointer;
}
.rm-cal-html-daynum {
  position: relative;
  z-index: 2;
  font-size: 0.875rem;
  font-weight: 600;
  color: #0f172a;
  line-height: 1.2;
  width: fit-content;
  padding: 0;
  background: transparent;
}
.rm-cal-html-daynum:hover { color: #2563eb; }
.rm-cal-html-hit {
  position: absolute;
  inset: 0;
  z-index: 1;
  opacity: 0;
  background: transparent;
  padding: 0;
}
.rm-cal-html-chips {
  position: relative;
  z-index: 3;
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
  margin-top: 0.1rem;
  min-height: 0;
  flex: 1;
}
.rm-cal-html-chip {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  min-width: 0;
  font-size: 0.75rem;
  line-height: 1.25rem;
  font-weight: 500;
  padding: 0.08rem 0.35rem;
  border-radius: 0.25rem;
  background: #dbeafe;
  color: #1d4ed8;
  text-align: left;
}
.rm-cal-html-chip-time {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.rm-cal-html-chip-title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rm-cal-html-chip:hover { filter: brightness(0.97); }
.rm-cal-html-chip.rm-chip-submission { background: #ffedd5; color: #c2410c; }
.rm-cal-html-chip.rm-chip-task { background: #f1f5f9; color: #475569; }
.rm-cal-html-chip.rm-chip-milestone { background: #fee2e2; color: #b91c1c; }
.rm-cal-html-chip.rm-chip-done {
  background: #f1f5f9;
  color: #64748b;
  text-decoration: line-through;
}
.rm-cal-html-chip.selected { box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.35); }
.rm-cal-html-more {
  position: relative;
  z-index: 3;
  font-size: 0.72rem;
  color: #64748b;
  margin-top: 0.05rem;
}
"""

CALENDAR_CLICK_JS = """
(function () {
  if (window.__rmCalClickBound) return;
  window.__rmCalClickBound = true;
  document.addEventListener("click", function (event) {
    var chip = event.target.closest(".rm-cal-html-chip");
    var cell = event.target.closest(".rm-cal-html-cell");
    var dateKey = "";
    var itemId = "";
    if (chip) {
      dateKey = chip.getAttribute("data-sched-date") || "";
      itemId = chip.getAttribute("data-sched-item") || "";
    } else if (cell) {
      dateKey = cell.getAttribute("data-sched-date") || "";
    } else {
      return;
    }
    if (!dateKey && !itemId) return;
    event.preventDefault();
    event.stopPropagation();
    var url = new URL(window.location.href);
    url.searchParams.set("sched_view", "1");
    if (dateKey) url.searchParams.set("sched_date", dateKey);
    else url.searchParams.delete("sched_date");
    if (itemId) url.searchParams.set("sched_item", itemId);
    else url.searchParams.delete("sched_item");
    window.location.assign(url.href);
  }, true);
})();
"""


def mount_schedule_calendar(html: str) -> dict | None:
    """Render the month grid in-page so day/chip clicks open the popup."""
    import streamlit as st

    payload = (
        f"<style>{CALENDAR_IFRAME_CSS}</style>"
        '<div class="rm-cal-card" id="rm-cal-root">'
        '<div class="rm-cal-card-title"><span>📅</span><span>일정 캘린더</span></div>'
        f"{html}</div>"
        f"<script>{CALENDAR_CLICK_JS}</script>"
    )
    st.html(payload, unsafe_allow_javascript=True)
    return None


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
