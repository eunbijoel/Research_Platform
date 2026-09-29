from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from format_reply import format_holidays
from service import (
    HolidayHit,
    HolidayQuery,
    MemoryService,
    detect_schedule_intent,
    month_bounds,
    parse_mentioned_month,
)


def test_month_bounds() -> None:
    start, end = month_bounds(date(2026, 9, 29))
    assert start == date(2026, 9, 1)
    assert end == date(2026, 9, 30)


def test_parse_mentioned_month() -> None:
    assert parse_mentioned_month("9월 일정", today=date(2026, 9, 29)) == date(2026, 9, 1)
    assert parse_mentioned_month("8월 일정", today=date(2026, 9, 29)) == date(2026, 8, 1)
    assert parse_mentioned_month("2025년 12월 일정") == date(2025, 12, 1)
    # day-specific should not be treated as month-only parse target for "9월 18일"
    assert parse_mentioned_month("9월 18일 미팅", today=date(2026, 9, 29)) is None


def test_lookup_chuseok() -> None:
    ms = MemoryService()
    q = ms.lookup_holidays("추석 날짜 알려줘", today=date(2026, 9, 29))
    assert q.items
    assert any(h.day == date(2026, 9, 25) for h in q.items)
    assert any("추석" in h.name for h in q.items)


def test_lookup_month_holidays() -> None:
    ms = MemoryService()
    q = ms.lookup_holidays("이번 달 공휴일", today=date(2026, 9, 1))
    assert "이번 달" in q.label
    assert any("추석" in h.name for h in q.items)


def test_format_holidays() -> None:
    text = format_holidays(
        HolidayQuery(
            label="추석 날짜",
            items=[
                HolidayHit(day=date(2026, 9, 24), name="추석 전날"),
                HolidayHit(day=date(2026, 9, 25), name="추석"),
            ],
        )
    )[0]
    assert "추석" in text
    assert "2026-09-25" in text
    assert "캘린더 기준" in text
