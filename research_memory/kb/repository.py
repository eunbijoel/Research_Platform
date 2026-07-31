from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from research_memory.config import DB_PATH, INDEX_PATH, ensure_data_dirs
from research_memory.kb.index import TfidfIndex


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeRepository:
    """SQLite metadata/facts/chunks + TF-IDF retrieval index."""

    def __init__(self, db_path: Path | None = None, index_path: Path | None = None):
        ensure_data_dirs()
        self.db_path = Path(db_path or DB_PATH)
        self.index_path = Path(index_path or INDEX_PATH)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_type TEXT,
                    content_hash TEXT UNIQUE,
                    stored_path TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    title TEXT,
                    project_id TEXT,
                    year INTEGER,
                    doc_type TEXT,
                    metadata_json TEXT,
                    full_text TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    location TEXT,
                    page INTEGER,
                    text TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    label TEXT,
                    value TEXT,
                    location TEXT,
                    confidence REAL,
                    FOREIGN KEY(document_id) REFERENCES documents(id)
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(document_id);
                CREATE INDEX IF NOT EXISTS idx_docs_project ON documents(project_id);
                """
            )

    def get_document_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return dict(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT d.*,
                       (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count,
                       (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) AS fact_count
                FROM documents d
                ORDER BY d.created_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_document(self, document_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self.rebuild_index()

    def insert_failed_document(
        self,
        *,
        filename: str,
        file_type: str,
        content_hash: str,
        stored_path: str,
        error: str,
        metadata: dict[str, Any],
    ) -> str:
        doc_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    id, filename, file_type, content_hash, stored_path, status, error,
                    title, project_id, year, doc_type, metadata_json, full_text, created_at
                ) VALUES (?, ?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?, '', ?)
                """,
                (
                    doc_id,
                    filename,
                    file_type,
                    content_hash,
                    stored_path,
                    error,
                    metadata.get("title", filename),
                    metadata.get("project_id", ""),
                    metadata.get("year"),
                    metadata.get("doc_type", "other"),
                    json.dumps(metadata, ensure_ascii=False),
                    _utc_now(),
                ),
            )
        return doc_id

    def insert_document(
        self,
        *,
        filename: str,
        file_type: str,
        content_hash: str,
        stored_path: str,
        full_text: str,
        metadata: dict[str, Any],
        facts: list[dict[str, Any]],
        chunks: list[dict[str, Any]],
    ) -> str:
        doc_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    id, filename, file_type, content_hash, stored_path, status, error,
                    title, project_id, year, doc_type, metadata_json, full_text, created_at
                ) VALUES (?, ?, ?, ?, ?, 'ready', '', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    filename,
                    file_type,
                    content_hash,
                    stored_path,
                    metadata.get("title", filename),
                    metadata.get("project_id", ""),
                    metadata.get("year"),
                    metadata.get("doc_type", "other"),
                    json.dumps(metadata, ensure_ascii=False),
                    full_text,
                    _utc_now(),
                ),
            )
            for ch in chunks:
                conn.execute(
                    """
                    INSERT INTO chunks (id, document_id, chunk_index, location, page, text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        doc_id,
                        ch["chunk_index"],
                        ch.get("location", ""),
                        ch.get("page"),
                        ch["text"],
                    ),
                )
            for fact in facts:
                conn.execute(
                    """
                    INSERT INTO facts (id, document_id, label, value, location, confidence)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        doc_id,
                        fact.get("label", ""),
                        fact.get("value", ""),
                        fact.get("location", ""),
                        float(fact.get("confidence", 0.5)),
                    ),
                )
        return doc_id

    def iter_chunks(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.location, c.page, c.text,
                       d.filename, d.title, d.project_id, d.doc_type, d.status
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'ready'
                ORDER BY d.created_at DESC, c.chunk_index ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def list_facts(self, document_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if document_id:
                rows = conn.execute(
                    "SELECT * FROM facts WHERE document_id = ? ORDER BY confidence DESC",
                    (document_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM facts ORDER BY confidence DESC LIMIT 200"
                ).fetchall()
            return [dict(r) for r in rows]

    def rebuild_index(self) -> int:
        chunks = self.iter_chunks()
        if not chunks:
            if self.index_path.exists():
                self.index_path.unlink()
            return 0
        TfidfIndex().fit(chunks).save(self.index_path)
        return len(chunks)

    def search(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if not self.index_path.exists():
            self.rebuild_index()
        if not self.index_path.exists():
            return []
        return TfidfIndex.load(self.index_path).search(query, top_k=top_k)
