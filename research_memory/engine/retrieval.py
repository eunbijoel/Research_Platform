from __future__ import annotations

from research_memory.config import RETRIEVAL_TOP_K
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import Citation, normalize_document_role


def retrieve(
    query: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = RETRIEVAL_TOP_K,
) -> list[Citation]:
    citations, _backend = retrieve_with_backend(query, repo=repo, top_k=top_k)
    return citations


def retrieve_with_backend(
    query: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = RETRIEVAL_TOP_K,
) -> tuple[list[Citation], str]:
    repo = repo or KnowledgeRepository()
    hits = repo.search(query, top_k=top_k)
    backend = str(hits[0].get("retrieval_backend", "none")) if hits else "none"
    citations: list[Citation] = []
    for hit in hits:
        snippet = hit["text"].strip()
        if len(snippet) > 420:
            snippet = snippet[:417] + "..."
        role = hit.get("document_role")
        if not role:
            doc = repo.get_document(str(hit.get("document_id") or ""))
            if doc:
                role = doc.get("document_role")
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
    return citations, backend
