from __future__ import annotations

from research_memory.config import RETRIEVAL_TOP_K
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import Citation, normalize_document_role


def retrieve(
    query: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = RETRIEVAL_TOP_K,
    exclude_project_ids: list[str] | set[str] | None = None,
    document_id: str | None = None,
) -> list[Citation]:
    citations, _backend = retrieve_with_backend(
        query,
        repo=repo,
        top_k=top_k,
        exclude_project_ids=exclude_project_ids,
        document_id=document_id,
    )
    return citations


def retrieve_with_backend(
    query: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = RETRIEVAL_TOP_K,
    exclude_project_ids: list[str] | set[str] | None = None,
    document_id: str | None = None,
) -> tuple[list[Citation], str]:
    repo = repo or KnowledgeRepository()
    excluded = {str(x).strip() for x in (exclude_project_ids or []) if str(x).strip()}
    fetch_k = max(top_k * 4, top_k) if excluded else top_k
    hits = repo.search(query, top_k=fetch_k, document_id=document_id)
    backend = str(hits[0].get("retrieval_backend", "none")) if hits else "none"
    citations: list[Citation] = []
    for hit in hits:
        doc_id = str(hit.get("document_id") or "")
        doc = repo.get_document(doc_id) or {}
        if excluded and not document_id:
            pid = str(doc.get("project_id") or "").strip()
            if pid and pid in excluded:
                continue
        snippet = (hit.get("text") or "").strip()
        if len(snippet) > 420:
            snippet = snippet[:417] + "..."
        role = hit.get("document_role") or doc.get("document_role")
        citations.append(
            Citation(
                document_id=hit["document_id"],
                filename=hit["filename"],
                location=hit.get("location") or "",
                snippet=snippet,
                score=float(hit.get("score") or 0.0),
                document_role=normalize_document_role(role),
            )
        )
        if len(citations) >= top_k:
            break
    return citations, backend
