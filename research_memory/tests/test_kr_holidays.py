from __future__ import annotations

from datetime import date

from research_memory.engine.kr_holidays import holiday_name, kr_holidays_for_year, short_holiday_label
from research_memory.engine.schedule import calendar_grid_sunday, render_calendar_html


def test_kr_holidays_include_seollal_2026() -> None:
    names = kr_holidays_for_year(2026)
    assert date(2026, 2, 17) in names
    assert "설날" in names[date(2026, 2, 17)]


def test_constitution_day_excluded() -> None:
    names = kr_holidays_for_year(2026)
    assert date(2026, 7, 17) not in names


def test_short_holiday_label() -> None:
    assert short_holiday_label("기독탄신일") == "성탄절"
    assert "대체" in short_holiday_label("광복절 대체 휴일")


def test_render_calendar_marks_holiday() -> None:
    grid = calendar_grid_sunday(2026, 3)
    html = render_calendar_html(
        year=2026,
        month=3,
        grid=grid,
        by_day={},
        today=date(2026, 3, 15),
    )
    assert "holiday" in html
    assert "삼일절" in html
    assert holiday_name(date(2026, 3, 1)) is not None
