from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from format_reply import format_schedule
from service import ScheduleQuery, week_bounds


def test_week_bounds_monday_to_sunday() -> None:
    # 2026-09-28 is Monday
    start, end = week_bounds(date(2026, 9, 28))
    assert start == date(2026, 9, 28)
    assert end == date(2026, 10, 4)


def test_week_bounds_midweek() -> None:
    # 2026-09-30 is Wednesday
    start, end = week_bounds(date(2026, 9, 30))
    assert start == date(2026, 9, 28)
    assert end == date(2026, 10, 4)


def test_format_schedule_empty() -> None:
    query = ScheduleQuery(
        label="오늘 (2026-09-28)",
        date_from="2026-09-28",
        date_to="2026-09-28",
        items=[],
    )
    text = format_schedule(query)[0]
    assert "오늘 (2026-09-28)" in text
    assert "등록된 일정이 없습니다." in text


def test_format_schedule_items() -> None:
    query = SimpleNamespace(
        label="이번 주 (2026-09-28 ~ 2026-10-04)",
        items=[
            {
                "title": "14:00 주간 회의",
                "event_type": "meeting",
                "status": "planned",
                "project_id": "proj-a",
                "date": "2026-09-28",
                "end_date": "2026-09-28",
                "note": "",
            },
            {
                "title": "중간보고서",
                "event_type": "submission",
                "status": "in_progress",
                "project_id": "proj-b",
                "date": "2026-09-30",
                "end_date": "2026-10-02",
                "note": "",
            },
        ],
    )
    text = format_schedule(query)[0]
    assert "이번 주" in text
    assert "주간 회의" in text
    assert "[회의/" in text
    assert "proj-a" in text
    assert "2026-09-30~2026-10-02" in text
    assert "중간보고서" in text
