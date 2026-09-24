from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import ChatLogStore, citations_payload


def test_citations_payload_truncates_snippet() -> None:
    cite = SimpleNamespace(
        to_dict=lambda: {
            "document_id": "d1",
            "filename": "note.md",
            "location": "p.1",
            "document_role": "project_document",
            "score": 0.9,
            "snippet": "가" * 500,
        }
    )
    rows = citations_payload([cite])
    assert len(rows) == 1
    assert rows[0]["filename"] == "note.md"
    assert len(rows[0]["snippet"]) == 200


def test_add_turn_roundtrip(tmp_path: Path) -> None:
    store = ChatLogStore(tmp_path / "chat.sqlite3")
    cite = SimpleNamespace(
        to_dict=lambda: {
            "document_id": "d1",
            "filename": "note.md",
            "location": "p.2",
            "document_role": "project_document",
            "score": 0.8,
            "snippet": "근거 문장",
        }
    )
    row_id = store.add_turn(
        telegram_user_id=111,
        telegram_chat_id=222,
        question="이 과제 목표가 뭐야?",
        answer="근거에 따라 답합니다.",
        refused=False,
        mode="llm",
        citations=[cite],
    )
    assert row_id == 1
    turns = store.latest(limit=5)
    assert len(turns) == 1
    turn = turns[0]
    assert turn.question == "이 과제 목표가 뭐야?"
    assert turn.answer == "근거에 따라 답합니다."
    assert turn.mode == "llm"
    assert turn.refused is False
    assert turn.citations[0]["filename"] == "note.md"


def test_refused_turn_has_empty_citations(tmp_path: Path) -> None:
    store = ChatLogStore(tmp_path / "chat.sqlite3")
    store.add_turn(
        telegram_user_id=1,
        telegram_chat_id=1,
        question="없는 내용",
        answer="메모리에 근거가 없어 답할 수 없습니다.",
        refused=True,
        mode="refused",
        citations=[],
    )
    turn = store.latest(1)[0]
    assert turn.refused is True
    assert turn.citations == []
