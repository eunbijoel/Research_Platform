from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from research_memory.config import DB_PATH, INDEX_PATH, VECTOR_INDEX_PATH, ensure_data_dirs
from research_memory.kb.embeddings import rebuild_retrieval_index, search_retrieval
from research_memory.schema import normalize_document_role


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enrich_document(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Expose document_role from metadata_json with a safe default."""
    if row is None:
        return None
    doc = dict(row)
    meta: dict[str, Any] = {}
    raw = doc.get("metadata_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                meta = parsed
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(raw, dict):
        meta = raw
    role = normalize_document_role(meta.get("document_role") or doc.get("document_role"))
    doc["document_role"] = role
    doc["metadata"] = meta
    return doc


class KnowledgeRepository:
    """SQLite metadata/facts/chunks + vector (primary) / TF-IDF (fallback) index."""

    def __init__(self, db_path: Path | None = None, index_path: Path | None = None):
        ensure_data_dirs()
        self.db_path = Path(db_path or DB_PATH)
        self.index_path = Path(index_path or INDEX_PATH)
        self.vector_index_path = VECTOR_INDEX_PATH
        self._init_db()
        self.last_index_status: dict[str, Any] = {}

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

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    owner TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    notes TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS milestones (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    due_date TEXT,
                    deliverable_type TEXT,
                    expected_keywords TEXT,
                    status TEXT NOT NULL DEFAULT 'planned',
                    linked_document_id TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );

                CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project_id);
                """
            )

    def get_document_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return _enrich_document(dict(row) if row else None)

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
            return [_enrich_document(dict(r)) for r in rows]  # type: ignore[misc]

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT d.*,
                       (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count,
                       (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id) AS fact_count
                FROM documents d
                WHERE d.id = ?
                """,
                (document_id,),
            ).fetchone()
            return _enrich_document(dict(row) if row else None)

    def list_chunks(self, document_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id AS chunk_id, document_id, chunk_index, location, page, text
                FROM chunks
                WHERE document_id = ?
                ORDER BY chunk_index ASC
                """,
                (document_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_document(self, document_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self.rebuild_index()

    def update_document(
        self,
        document_id: str,
        *,
        title: str | None = None,
        project_id: str | None = None,
        full_text: str | None = None,
        document_role: str | None = None,
        doc_type: str | None = None,
    ) -> None:
        """Update metadata and/or body text; re-chunk when full_text changes."""
        from research_memory.pipeline.chunking import refine_chunks
        from research_memory.schema import TextChunk

        doc = self.get_document(document_id)
        if not doc:
            raise ValueError(f"document not found: {document_id}")

        new_title = doc.get("title") if title is None else title
        new_project = doc.get("project_id") if project_id is None else project_id
        new_text = doc.get("full_text") if full_text is None else full_text
        new_doc_type = doc.get("doc_type") if doc_type is None else doc_type

        meta = dict(doc.get("metadata") or {})
        if document_role is not None:
            meta["document_role"] = normalize_document_role(document_role)
        if doc_type is not None:
            meta["doc_type"] = doc_type
        if title is not None:
            meta["title"] = title
        if project_id is not None:
            meta["project_id"] = project_id

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE documents
                SET title = ?, project_id = ?, full_text = ?, doc_type = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    new_title or "",
                    new_project or "",
                    new_text or "",
                    new_doc_type or "other",
                    json.dumps(meta, ensure_ascii=False),
                    document_id,
                ),
            )
            if full_text is not None:
                conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
                raw = [
                    TextChunk(
                        text=new_text or "",
                        location="edited",
                        chunk_index=0,
                    )
                ]
                for ch in refine_chunks(raw):
                    conn.execute(
                        """
                        INSERT INTO chunks (id, document_id, chunk_index, location, page, text)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            document_id,
                            ch.chunk_index,
                            ch.location,
                            ch.page,
                            ch.text,
                        ),
                    )
        if full_text is not None:
            self.rebuild_index()

    def save_document_insight(
        self,
        document_id: str,
        insight: dict[str, Any],
        *,
        sync_doc_type: bool = True,
    ) -> None:
        """Merge document_insight into metadata_json. No re-chunk / re-embed."""
        doc = self.get_document(document_id)
        if not doc:
            raise ValueError(f"document not found: {document_id}")
        meta = dict(doc.get("metadata") or {})
        meta["document_insight"] = insight
        dtype = str(insight.get("document_type") or "").strip().lower()
        new_doc_type = doc.get("doc_type") or "other"
        if sync_doc_type and dtype:
            meta["doc_type"] = dtype
            new_doc_type = dtype
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE documents
                SET metadata_json = ?, doc_type = ?
                WHERE id = ?
                """,
                (json.dumps(meta, ensure_ascii=False), new_doc_type, document_id),
            )

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
                       d.filename, d.title, d.project_id, d.doc_type, d.status, d.metadata_json
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.status = 'ready'
                ORDER BY d.created_at DESC, c.chunk_index ASC
                """
            ).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                meta: dict[str, Any] = {}
                raw = item.get("metadata_json")
                if isinstance(raw, str) and raw.strip():
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            meta = parsed
                    except json.JSONDecodeError:
                        meta = {}
                item["document_role"] = normalize_document_role(meta.get("document_role"))
                out.append(item)
            return out

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
        self.last_index_status = rebuild_retrieval_index(chunks)
        return int(self.last_index_status.get("chunk_count") or 0)

    def search(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if not VECTOR_INDEX_PATH.exists() and not INDEX_PATH.exists():
            self.rebuild_index()
        hits, backend = search_retrieval(query, top_k=top_k, prefer_vector=True)
        for h in hits:
            h["retrieval_backend"] = backend
        return hits

    def retrieval_status(self) -> dict[str, Any]:
        return {
            "vector_index": VECTOR_INDEX_PATH.exists(),
            "tfidf_index": INDEX_PATH.exists(),
            "last_rebuild": self.last_index_status,
        }

    # --- Phase 4: projects / milestones (Tracking) ---

    def upsert_project(
        self,
        *,
        project_id: str,
        title: str,
        owner: str = "",
        start_date: str = "",
        end_date: str = "",
        status: str = "active",
        notes: str = "",
    ) -> str:
        project_id = project_id.strip()
        if not project_id:
            raise ValueError("project_id required")
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE projects
                    SET title=?, owner=?, start_date=?, end_date=?, status=?, notes=?
                    WHERE project_id=?
                    """,
                    (title, owner, start_date, end_date, status, notes, project_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO projects (
                        project_id, title, owner, start_date, end_date, status, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        title,
                        owner,
                        start_date,
                        end_date,
                        status,
                        notes,
                        _utc_now(),
                    ),
                )
        return project_id

    def list_projects(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT p.*,
                       (SELECT COUNT(*) FROM milestones m WHERE m.project_id = p.project_id) AS milestone_count,
                       (SELECT COUNT(*) FROM documents d
                        WHERE d.project_id = p.project_id AND d.status = 'ready') AS document_count
                FROM projects p
                ORDER BY p.created_at DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return dict(row) if row else None

    def delete_project(self, project_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM milestones WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))

    def delete_project_folder(self, project_id: str) -> dict[str, Any]:
        """Delete project registry + all documents/chunks/facts/milestones for that project."""
        project_id = (project_id or "").strip()
        if not project_id:
            raise ValueError("project_id required")
        with self._conn() as conn:
            doc_rows = conn.execute(
                "SELECT id FROM documents WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            doc_ids = [r["id"] for r in doc_rows]
            for doc_id in doc_ids:
                conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
                conn.execute("DELETE FROM facts WHERE document_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM milestones WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        if doc_ids:
            self.rebuild_index()
        return {"ok": True, "project_id": project_id, "deleted_documents": len(doc_ids)}

    def rename_project(self, old_project_id: str, new_project_id: str) -> dict[str, Any]:
        """Rename a project folder and retarget documents/milestones."""
        old_id = (old_project_id or "").strip()
        new_id = (new_project_id or "").strip()
        if not old_id or not new_id:
            raise ValueError("old/new project_id required")
        if old_id == new_id:
            return {"ok": True, "project_id": new_id, "renamed": False}
        with self._conn() as conn:
            clash = conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (new_id,),
            ).fetchone()
            if clash:
                raise ValueError(f"project already exists: {new_id}")
            existing = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (old_id,),
            ).fetchone()
            if existing:
                row = dict(existing)
                conn.execute(
                    """
                    INSERT INTO projects (
                        project_id, title, owner, start_date, end_date, status, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        new_id if (row.get("title") or "") == old_id else (row.get("title") or new_id),
                        row.get("owner") or "",
                        row.get("start_date") or "",
                        row.get("end_date") or "",
                        row.get("status") or "active",
                        row.get("notes") or "",
                        row.get("created_at") or _utc_now(),
                    ),
                )
                conn.execute(
                    "UPDATE milestones SET project_id = ? WHERE project_id = ?",
                    (new_id, old_id),
                )
                conn.execute("DELETE FROM projects WHERE project_id = ?", (old_id,))
            else:
                # Project may exist only via documents (no projects-table row).
                conn.execute(
                    """
                    INSERT INTO projects (
                        project_id, title, owner, start_date, end_date, status, notes, created_at
                    ) VALUES (?, ?, '', '', '', 'active', '', ?)
                    """,
                    (new_id, new_id, _utc_now()),
                )
            conn.execute(
                "UPDATE documents SET project_id = ? WHERE project_id = ?",
                (new_id, old_id),
            )
        return {"ok": True, "project_id": new_id, "from": old_id, "renamed": True}

    def add_milestone(
        self,
        *,
        project_id: str,
        title: str,
        due_date: str = "",
        deliverable_type: str = "other",
        expected_keywords: str = "",
        status: str = "planned",
        notes: str = "",
        linked_document_id: str = "",
    ) -> str:
        mid = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO milestones (
                    id, project_id, title, due_date, deliverable_type, expected_keywords,
                    status, linked_document_id, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    project_id,
                    title,
                    due_date,
                    deliverable_type,
                    expected_keywords,
                    status,
                    linked_document_id or None,
                    notes,
                    _utc_now(),
                ),
            )
        return mid

    def update_milestone(self, milestone_id: str, **fields: Any) -> None:
        allowed = {
            "title",
            "due_date",
            "deliverable_type",
            "expected_keywords",
            "status",
            "linked_document_id",
            "notes",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        cols = ", ".join(f"{k}=?" for k in updates)
        values = list(updates.values()) + [milestone_id]
        with self._conn() as conn:
            conn.execute(f"UPDATE milestones SET {cols} WHERE id=?", values)

    def delete_milestone(self, milestone_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))

    def list_milestones(self, project_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM milestones
                WHERE project_id = ?
                ORDER BY CASE WHEN due_date IS NULL OR due_date = '' THEN 1 ELSE 0 END,
                         due_date ASC, created_at ASC
                """,
                (project_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_documents_for_project(self, project_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM documents
                WHERE project_id = ? AND status = 'ready'
                ORDER BY created_at ASC
                """,
                (project_id,),
            ).fetchall()
            return [_enrich_document(dict(r)) for r in rows]  # type: ignore[misc]
