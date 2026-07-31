from __future__ import annotations

from research_memory.config import RETRIEVAL_TOP_K
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import Citation


def retrieve(
    query: str,
    *,
    repo: KnowledgeRepository | None = None,
    top_k: int = RETRIEVAL_TOP_K,
) -> list[Citation]:
    repo = repo or KnowledgeRepository()
    hits = repo.search(query, top_k=top_k)
    citations: list[Citation] = []
    for hit in hits:
        snippet = hit["text"].strip()
        if len(snippet) > 420:
            snippet = snippet[:417] + "..."
        citations.append(
            Citation(
                document_id=hit["document_id"],
                filename=hit["filename"],
                location=hit.get("location") or "",
                snippet=snippet,
                score=float(hit.get("score") or 0.0),
            )
        )
    return citations
