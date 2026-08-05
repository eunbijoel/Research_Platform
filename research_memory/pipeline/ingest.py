from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from research_memory.config import RAW_DIR, ensure_data_dirs
from research_memory.kb.repository import KnowledgeRepository
from research_memory.pipeline.chunking import refine_chunks
from research_memory.pipeline.extractors import extract_chunks
from research_memory.pipeline.metadata import extract_metadata_and_facts
from research_memory.schema import ParsedDocument, normalize_document_role


def ingest_file(
    source_path: Path,
    *,
    repo: KnowledgeRepository | None = None,
    project_id: str = "",
    document_role: str = "project_document",
    copy_to_raw: bool = True,
) -> dict:
    """
    Pipeline → Metadata/Facts → Knowledge Base.

    Returns a status dict suitable for UI / CLI.
    """
    ensure_data_dirs()
    repo = repo or KnowledgeRepository()
    source_path = Path(source_path)
    if not source_path.exists():
        return {"ok": False, "error": f"File not found: {source_path}"}

    file_bytes = source_path.read_bytes()
    digest = hashlib.sha256(file_bytes).hexdigest()
    existing = repo.get_document_by_hash(digest)
    if existing:
        return {
            "ok": True,
            "skipped": True,
            "document_id": existing["id"],
            "filename": existing["filename"],
            "document_role": existing.get("document_role") or "project_document",
            "message": "Already ingested (same content hash).",
        }

    stored_path = source_path
    if copy_to_raw:
        dest = RAW_DIR / source_path.name
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
            dest = RAW_DIR / f"{digest[:10]}_{source_path.name}"
        if not dest.exists():
            shutil.copy2(source_path, dest)
        stored_path = dest

    file_type, raw_chunks, err = extract_chunks(stored_path)
    if err and not raw_chunks:
        return {
            "ok": False,
            "error": err,
            "filename": source_path.name,
            "status": "failed",
        }

    chunks = refine_chunks(raw_chunks)
    meta, facts = extract_metadata_and_facts(source_path.name, file_type, chunks)
    if project_id:
        meta.project_id = project_id
        meta.extra["project_id_override"] = project_id
    meta.document_role = normalize_document_role(document_role)

    full_text = "\n\n".join(c.text for c in chunks)
    parsed = ParsedDocument(
        filename=source_path.name,
        file_type=file_type,
        full_text=full_text,
        chunks=chunks,
        metadata=meta,
        facts=facts,
        ok=bool(chunks),
        error=err or ("" if chunks else "No text extracted"),
    )

    if not parsed.ok:
        doc_id = repo.insert_failed_document(
            filename=source_path.name,
            file_type=file_type,
            content_hash=digest,
            stored_path=str(stored_path),
            error=parsed.error,
            metadata=meta.to_dict(),
        )
        return {
            "ok": False,
            "document_id": doc_id,
            "filename": source_path.name,
            "status": "failed",
            "error": parsed.error,
            "document_role": meta.document_role,
        }

    doc_id = repo.insert_document(
        filename=source_path.name,
        file_type=file_type,
        content_hash=digest,
        stored_path=str(stored_path),
        full_text=full_text,
        metadata=meta.to_dict(),
        facts=[f.to_dict() for f in facts],
        chunks=[
            {
                "chunk_index": c.chunk_index,
                "location": c.location,
                "page": c.page,
                "text": c.text,
            }
            for c in chunks
        ],
    )
    repo.rebuild_index()
    return {
        "ok": True,
        "skipped": False,
        "document_id": doc_id,
        "filename": source_path.name,
        "status": "ready",
        "chunks": len(chunks),
        "facts": len(facts),
        "metadata": meta.to_dict(),
        "document_role": meta.document_role,
    }


def ingest_bytes(
    data: bytes,
    filename: str,
    *,
    repo: KnowledgeRepository | None = None,
    project_id: str = "",
    document_role: str = "project_document",
) -> dict:
    ensure_data_dirs()
    tmp = RAW_DIR / filename
    # Avoid clobbering different content with same name
    digest = hashlib.sha256(data).hexdigest()
    if tmp.exists() and hashlib.sha256(tmp.read_bytes()).hexdigest() != digest:
        tmp = RAW_DIR / f"{digest[:10]}_{filename}"
    tmp.write_bytes(data)
    return ingest_file(
        tmp,
        repo=repo,
        project_id=project_id,
        document_role=document_role,
        copy_to_raw=False,
    )
