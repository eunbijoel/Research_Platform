from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telethon_user import is_allowed_sender, is_private_incoming_dm
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


def test_is_private_incoming_dm() -> None:
    assert is_private_incoming_dm(SimpleNamespace(out=False, is_private=True)) is True
    assert is_private_incoming_dm(SimpleNamespace(out=True, is_private=True)) is False
    assert is_private_incoming_dm(SimpleNamespace(out=False, is_private=False)) is False
    assert is_private_incoming_dm(SimpleNamespace(out=False, is_private=None, chat_id=12345)) is True
    assert is_private_incoming_dm(SimpleNamespace(out=False, is_private=None, chat_id=-100123)) is False


def test_is_allowed_sender() -> None:
    allowed = frozenset({111, 222})
    assert is_allowed_sender(111, allowed) is True
    assert is_allowed_sender(999, allowed) is False
    assert is_allowed_sender(None, allowed) is False
