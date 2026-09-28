from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from format_reply import format_meeting
from service import (
    MeetingContext,
    detect_schedule_intent,
    parse_mentioned_date,
)


def test_detect_meeting_notes_intent() -> None:
    assert detect_schedule_intent("그날 어떤 대화 나눴지?") == "meeting_notes"
    assert detect_schedule_intent("회의록 기준으로 안건 요약해줘") == "meeting_notes"
    assert detect_schedule_intent("뭐 얘기했어?") == "meeting_notes"


def test_detect_last_meeting_intent() -> None:
    assert detect_schedule_intent("마지막 미팅이 뭐였지?") == "last_meeting"
    assert detect_schedule_intent("최근 회의") == "last_meeting"
    assert detect_schedule_intent("last meeting") == "last_meeting"


def test_detect_today_week_intent() -> None:
    assert detect_schedule_intent("오늘 일정 뭐 있어?") == "today"
    assert detect_schedule_intent("이번 주 일정") == "week"


def test_detect_schedule_no_intent() -> None:
    assert detect_schedule_intent("Manufacturing-X 목표가 뭐야?") is None
    assert detect_schedule_intent("프로젝트 목록") is None


def test_parse_mentioned_date() -> None:
    assert parse_mentioned_date("2026-09-18 회의 내용") == date(2026, 9, 18)
    assert parse_mentioned_date("9월 18일 미팅", today=date(2026, 9, 28)) == date(
        2026, 9, 18
    )


def test_format_meeting_with_attachment() -> None:
    ctx = MeetingContext(
        item={
            "title": "14:00 생기원 회의",
            "event_type": "meeting",
            "status": "planned",
            "project_id": "Manufacturing-X",
            "date": "2026-09-18",
            "end_date": "2026-09-18",
            "note": "",
        },
        attachments=[
            {
                "id": "d1",
                "title": "회의록",
                "filename": "notes.hwp",
                "status": "ready",
            }
        ],
    )
    text = format_meeting(ctx)[0]
    assert "생기원 회의" in text
    assert "첨부 회의록 1건" in text
    assert "회의록" in text or "notes.hwp" in text


def test_format_meeting_none() -> None:
    text = format_meeting(None)[0]
    assert "찾지 못했습니다" in text


def test_format_meeting_no_attachments() -> None:
    ctx = SimpleNamespace(
        item={
            "title": "인터엑스 회의",
            "event_type": "meeting",
            "status": "planned",
            "project_id": "Manufacturing-X",
            "date": "2026-09-17",
            "end_date": "",
            "note": "",
        },
        attachments=[],
    )
    text = format_meeting(ctx)[0]
    assert "첨부 회의록: 없음" in text
