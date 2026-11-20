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


@dataclass(frozen=True)
class AllowedTelegramUser:
    telegram_user_id: int
    display_name: str
    created_at: str


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS allowed_telegram_users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    display_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_private_dms (
                    telegram_user_id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_notices (
                    schedule_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    anchor_date TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (schedule_id, kind, anchor_date, telegram_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_lock (
                    name TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    heartbeat TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO telegram_private_dms (telegram_user_id, started_at)
                SELECT telegram_user_id, MIN(created_at)
                FROM chat_turns
                WHERE telegram_user_id = telegram_chat_id
                GROUP BY telegram_user_id
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

    def is_allowed_user(self, telegram_user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM allowed_telegram_users WHERE telegram_user_id = ? LIMIT 1",
                (int(telegram_user_id),),
            ).fetchone()
        return row is not None

    def list_allowed_users(self) -> list[AllowedTelegramUser]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT telegram_user_id, display_name, created_at
                FROM allowed_telegram_users
                ORDER BY created_at ASC, telegram_user_id ASC
                """
            ).fetchall()
        return [
            AllowedTelegramUser(
                telegram_user_id=int(row["telegram_user_id"]),
                display_name=str(row["display_name"] or ""),
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def add_allowed_user(self, telegram_user_id: int, display_name: str = "") -> None:
        uid = int(telegram_user_id)
        name = (display_name or "").strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO allowed_telegram_users (telegram_user_id, display_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    display_name = excluded.display_name
                """,
                (uid, name, _utc_now()),
            )
            conn.commit()

    def note_private_dm(self, telegram_user_id: int) -> None:
        """Remember that this user opened a private chat with the bot."""
        uid = int(telegram_user_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_private_dms (telegram_user_id, started_at)
                VALUES (?, ?)
                ON CONFLICT(telegram_user_id) DO NOTHING
                """,
                (uid, _utc_now()),
            )
            conn.commit()

    def list_private_dm_user_ids(self) -> set[int]:
        with self._connect() as conn:
            noted = conn.execute(
                "SELECT telegram_user_id FROM telegram_private_dms"
            ).fetchall()
            turned = conn.execute(
                """
                SELECT DISTINCT telegram_user_id FROM chat_turns
                WHERE telegram_user_id = telegram_chat_id
                """
            ).fetchall()
        return {int(row["telegram_user_id"]) for row in (*noted, *turned)}

    def notice_status(
        self,
        *,
        schedule_id: str,
        kind: str,
        anchor_date: str,
        telegram_user_id: int,
    ) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status FROM schedule_notices
                WHERE schedule_id = ? AND kind = ? AND anchor_date = ?
                  AND telegram_user_id = ?
                """,
                (schedule_id, kind, anchor_date, int(telegram_user_id)),
            ).fetchone()
        return str(row["status"]) if row else None

    def claim_notice(
        self,
        *,
        schedule_id: str,
        kind: str,
        anchor_date: str,
        telegram_user_id: int,
        owner: str,
        stale_seconds: int = 600,
    ) -> bool:
        """Reserve one delivery. False when it was already sent or is in flight."""
        now = datetime.now(timezone.utc)
        stamp = now.isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, owner, updated_at FROM schedule_notices
                WHERE schedule_id = ? AND kind = ? AND anchor_date = ?
                  AND telegram_user_id = ?
                """,
                (schedule_id, kind, anchor_date, int(telegram_user_id)),
            ).fetchone()
            if row is not None and str(row["status"]) == "sent":
                return False
            if row is not None and str(row["status"]) == "sending":
                try:
                    updated = datetime.fromisoformat(str(row["updated_at"]))
                except ValueError:
                    updated = now
                age = (now - updated).total_seconds()
                if str(row["owner"]) != owner and age < stale_seconds:
                    return False
            conn.execute(
                """
                INSERT INTO schedule_notices (
                    schedule_id, kind, anchor_date, telegram_user_id,
                    status, error, owner, updated_at
                ) VALUES (?, ?, ?, ?, 'sending', '', ?, ?)
                ON CONFLICT(schedule_id, kind, anchor_date, telegram_user_id) DO UPDATE SET
                    status = 'sending',
                    error = '',
                    owner = excluded.owner,
                    updated_at = excluded.updated_at
                """,
                (schedule_id, kind, anchor_date, int(telegram_user_id), owner, stamp),
            )
            conn.commit()
            return True

    def finish_notice(
        self,
        *,
        schedule_id: str,
        kind: str,
        anchor_date: str,
        telegram_user_id: int,
        status: str,
        error: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE schedule_notices
                SET status = ?, error = ?, updated_at = ?
                WHERE schedule_id = ? AND kind = ? AND anchor_date = ?
                  AND telegram_user_id = ?
                """,
                (
                    status,
                    (error or "")[:200],
                    _utc_now(),
                    schedule_id,
                    kind,
                    anchor_date,
                    int(telegram_user_id),
                ),
            )
            conn.commit()

    def acquire_scheduler(self, owner: str, *, stale_seconds: int = 600) -> bool:
        """Single-process lock for the daily notifier. Stale locks can be taken over."""
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, heartbeat FROM scheduler_lock WHERE name = 'schedule_notifier'"
            ).fetchone()
            if row is not None and str(row["owner"]) != owner:
                try:
                    beat = datetime.fromisoformat(str(row["heartbeat"]))
                except ValueError:
                    beat = now
                if (now - beat).total_seconds() < stale_seconds:
                    return False
            conn.execute(
                """
                INSERT INTO scheduler_lock (name, owner, heartbeat)
                VALUES ('schedule_notifier', ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    owner = excluded.owner,
                    heartbeat = excluded.heartbeat
                """,
                (owner, now.isoformat()),
            )
            conn.commit()
            return True

    def touch_scheduler(self, owner: str) -> bool:
        now = _utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE scheduler_lock
                SET heartbeat = ?
                WHERE name = 'schedule_notifier' AND owner = ?
                """,
                (now, owner),
            )
            conn.commit()
            return cur.rowcount > 0

    def release_scheduler(self, owner: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM scheduler_lock
                WHERE name = 'schedule_notifier' AND owner = ?
                """,
                (owner,),
            )
            conn.commit()

    def remove_allowed_user(self, telegram_user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM allowed_telegram_users WHERE telegram_user_id = ?",
                (int(telegram_user_id),),
            )
            conn.commit()
            return cur.rowcount > 0

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
