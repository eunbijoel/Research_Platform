"""Bot-only chat log. Does not write to Research Memory."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "chat.sqlite3"

SNIPPET_MAX = 200


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def citations_payload(citations: list[Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cite in citations or []:
        raw = cite.to_dict() if hasattr(cite, "to_dict") else dict(cite)
        snippet = str(raw.get("snippet") or "")[:SNIPPET_MAX]
        rows.append(
            {
                "document_id": raw.get("document_id") or "",
                "filename": raw.get("filename") or "",
                "location": raw.get("location") or "",
                "document_role": raw.get("document_role") or "",
                "score": raw.get("score"),
                "snippet": snippet,
            }
        )
    return rows


@dataclass(frozen=True)
class ChatTurn:
    id: int
    created_at: str
    telegram_user_id: int
    telegram_chat_id: int
    question: str
    answer: str
    refused: bool
    mode: str
    citations: list[dict[str, Any]]


class ChatLogStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    telegram_chat_id INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    refused INTEGER NOT NULL DEFAULT 0,
                    mode TEXT NOT NULL,
                    citations_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            conn.commit()

    def add_turn(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        question: str,
        answer: str,
        refused: bool = False,
        mode: str = "llm",
        citations: list[Any] | None = None,
    ) -> int:
        payload = citations_payload(citations)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO chat_turns (
                    created_at, telegram_user_id, telegram_chat_id,
                    question, answer, refused, mode, citations_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now(),
                    telegram_user_id,
                    telegram_chat_id,
                    question,
                    answer,
                    1 if refused else 0,
                    mode,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def latest(self, limit: int = 20) -> list[ChatTurn]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chat_turns
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_turn(row) for row in rows]

    def close(self) -> None:
        return None

    @staticmethod
    def _row_to_turn(row: sqlite3.Row) -> ChatTurn:
        raw = row["citations_json"] or "[]"
        try:
            citations = json.loads(raw)
        except json.JSONDecodeError:
            citations = []
        if not isinstance(citations, list):
            citations = []
        return ChatTurn(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            telegram_user_id=int(row["telegram_user_id"]),
            telegram_chat_id=int(row["telegram_chat_id"]),
            question=str(row["question"]),
            answer=str(row["answer"]),
            refused=bool(row["refused"]),
            mode=str(row["mode"]),
            citations=citations,
        )
