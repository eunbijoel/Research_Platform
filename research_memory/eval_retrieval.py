from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research_memory.config import PROJECT_ROOT
from research_memory.engine.retrieval import retrieve_with_backend
from research_memory.kb.repository import KnowledgeRepository

DEFAULT_GOLD = PROJECT_ROOT / "eval" / "gold_qa.json"


def load_gold(path: Path | None = None) -> list[dict[str, Any]]:
    path = Path(path or DEFAULT_GOLD)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("gold_qa.json must be a list")
    return data


def evaluate_retrieval(
    *,
    repo: KnowledgeRepository | None = None,
    gold_path: Path | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """
    Evaluate filename recall@k / MRR on a small gold set.

    Gold item shape:
      {"id": "...", "question": "...", "expected_files": ["a.md", ...]}
    """
    repo = repo or KnowledgeRepository()
    gold = load_gold(gold_path)
    rows: list[dict[str, Any]] = []
    hit_at_k = 0
    mrr_total = 0.0

    for item in gold:
        q = item["question"]
        expected = {Path(f).name for f in item.get("expected_files", [])}
        cites, backend = retrieve_with_backend(q, repo=repo, top_k=top_k)
        ranked = [c.filename for c in cites]
        rank = None
        for i, name in enumerate(ranked, start=1):
            if name in expected:
                rank = i
                break
        if rank is not None:
            hit_at_k += 1
            mrr_total += 1.0 / rank
        rows.append(
            {
                "id": item.get("id"),
                "question": q,
                "expected_files": sorted(expected),
                "retrieved_files": ranked,
                "hit": rank is not None,
                "rank": rank,
                "backend": backend,
                "top_score": cites[0].score if cites else 0.0,
            }
        )

    n = max(len(gold), 1)
    return {
        "n": len(gold),
        "top_k": top_k,
        "recall_at_k": hit_at_k / n,
        "mrr": mrr_total / n,
        "hits": hit_at_k,
        "rows": rows,
        "index": repo.retrieval_status(),
    }
