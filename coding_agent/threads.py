"""Persistent chat threads (UI messages) alongside LangGraph checkpointer."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ThreadStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.data_dir / "threads.json"
        self.messages_dir = self.data_dir / "messages"
        self.messages_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _save_index(self, rows: list[dict[str, Any]]) -> None:
        self.index_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_threads(self) -> list[dict[str, Any]]:
        rows = self._load_index()
        return sorted(rows, key=lambda r: r.get("updated_at") or "", reverse=True)

    def create(self, *, title: str = "New thread", model: str = "") -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()),
            "title": title,
            "model": model,
            "created_at": _now(),
            "updated_at": _now(),
        }
        rows = self._load_index()
        rows.append(row)
        self._save_index(rows)
        self.save_messages(row["id"], [])
        return row

    def touch(self, thread_id: str, *, title: str | None = None, model: str | None = None) -> None:
        rows = self._load_index()
        for row in rows:
            if row.get("id") == thread_id:
                row["updated_at"] = _now()
                if title:
                    row["title"] = title[:80]
                if model:
                    row["model"] = model
                break
        self._save_index(rows)

    def delete(self, thread_id: str) -> None:
        rows = [r for r in self._load_index() if r.get("id") != thread_id]
        self._save_index(rows)
        path = self.messages_dir / f"{thread_id}.json"
        if path.exists():
            path.unlink()

    def load_messages(self, thread_id: str) -> list[dict[str, Any]]:
        path = self.messages_dir / f"{thread_id}.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def save_messages(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        path = self.messages_dir / f"{thread_id}.json"
        path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
