"""Korea public holidays for the schedule calendar."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

# Library sometimes includes commemorative days that are not days off.
_EXCLUDE_NAME_PARTS = ("제헌절",)


@lru_cache(maxsize=16)
def kr_holidays_for_year(year: int) -> dict[date, str]:
    """Return {date: Korean name} for rest-day style public holidays."""
    try:
        import holidays
    except ImportError:
        return {}
    raw = holidays.country_holidays("KR", years=year, language="ko")
    out: dict[date, str] = {}
    for day, name in raw.items():
        label = str(name or "").strip()
        if not label:
            continue
        if any(part in label for part in _EXCLUDE_NAME_PARTS):
            continue
        out[day] = label
    return out


def holiday_name(day: date) -> str | None:
    return kr_holidays_for_year(day.year).get(day)


def short_holiday_label(name: str) -> str:
    """Compact label for calendar cells."""
    primary = (name or "").split(";")[0].strip()
    primary = primary.replace(" 대체 휴일", " 대체").replace("대체 휴일", "대체")
    primary = primary.replace("신정연휴", "신정")
    primary = primary.replace("기독탄신일", "성탄절")
    primary = primary.replace("지방선거일", "선거일")
    if len(primary) > 8:
        return primary[:8] + "…"
    return primary
