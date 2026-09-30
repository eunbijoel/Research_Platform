from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from user_router import answer_question_chunks, normalize_question


def test_normalize_slash_commands() -> None:
    assert normalize_question("/today") == "오늘 일정"
    assert normalize_question("/month") == "이번 달 일정"
    assert normalize_question("/projects") == "프로젝트 목록"
    assert normalize_question("추석 날짜") == "추석 날짜"


def test_answer_routes_month_schedule() -> None:
    memory = MagicMock()
    memory.list_month.return_value = SimpleNamespace(
        label="2026-09 일정",
        items=[],
    )
    chunks = answer_question_chunks(memory, "이번달 일정")
    assert chunks
    assert "일정" in chunks[0]
    memory.list_month.assert_called_once()
    memory.ask.assert_not_called()


def test_answer_routes_rag_ask() -> None:
    memory = MagicMock()
    memory.ask.return_value = SimpleNamespace(
        answer="근거에 따른 답",
        refused=False,
        citations=[],
    )
    memory.repo = None
    chunks = answer_question_chunks(memory, "KMX 목표가 뭐야?")
    assert "근거에 따른 답" in chunks[0]
    memory.ask.assert_called_once()


def test_strip_trigger_helper() -> None:
    from telethon_user import _strip_trigger

    assert _strip_trigger("/rm 이번달 일정", "/rm") == "이번달 일정"
    assert _strip_trigger("/RM: 추석", "/rm") == "추석"
    assert _strip_trigger("이번달 일정", "/rm") is None
    assert _strip_trigger("이번달 일정", "") == "이번달 일정"
