from __future__ import annotations

import json
import math
import pickle
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from research_memory.config import (
    EMBED_BATCH_SIZE,
    EMBED_MODEL,
    EMBED_TIMEOUT_SEC,
    INDEX_PATH,
    OLLAMA_BASE_URL,
    VECTOR_INDEX_PATH,
)
from research_memory.kb.index import TfidfIndex


class EmbeddingError(Exception):
    pass


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Embed texts via Ollama /api/embed. Raises EmbeddingError on failure."""
    if not texts:
        return []
    model = model or EMBED_MODEL
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        vectors.extend(_embed_batch(batch, model=model))
    return vectors


def embed_query(text: str, *, model: str | None = None) -> list[float]:
    vecs = embed_texts([text], model=model)
    if not vecs:
        raise EmbeddingError("Empty embedding for query")
    return vecs[0]


def embeddings_available(*, model: str | None = None) -> bool:
    try:
        embed_texts(["ping"], model=model)
        return True
    except EmbeddingError:
        return False


def _embed_batch(texts: list[str], *, model: str) -> list[list[float]]:
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/embed"
    payload = {"model": model, "input": texts}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        # Fallback to legacy single-prompt endpoint
        if exc.code in {404, 405}:
            return [_embed_one_legacy(t, model=model) for t in texts]
        raise EmbeddingError(f"Ollama embed HTTP {exc.code}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(f"Ollama embed failed: {exc}") from exc

    embeddings = body.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        raise EmbeddingError("Unexpected /api/embed response shape")
    return [[float(x) for x in row] for row in embeddings]


def _embed_one_legacy(text: str, *, model: str) -> list[float]:
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/embeddings"
    payload = {"model": model, "prompt": text}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT_SEC) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(f"Ollama embeddings failed: {exc}") from exc
    emb = body.get("embedding")
    if not isinstance(emb, list):
        raise EmbeddingError("Unexpected /api/embeddings response")
    return [float(x) for x in emb]


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


class VectorIndex:
    """Chunk metadata + dense embedding matrix (cosine via normalized vectors)."""

    def __init__(self) -> None:
        self.chunks: list[dict[str, Any]] = []
        self.vectors: list[list[float]] = []
        self.model: str = EMBED_MODEL
        self.backend: str = "ollama"

    def fit(
        self,
        chunks: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> "VectorIndex":
        self.model = model or EMBED_MODEL
        self.chunks = chunks
        texts = [c.get("text", "") for c in chunks]
        raw = embed_texts(texts, model=self.model)
        self.vectors = [_l2_normalize(v) for v in raw]
        self.backend = "ollama"
        return self

    def search(self, query: str, top_k: int = 6) -> list[dict[str, Any]]:
        if not query.strip() or not self.chunks or not self.vectors:
            return []
        q = _l2_normalize(embed_query(query, model=self.model))
        scored: list[tuple[float, int]] = []
        for i, vec in enumerate(self.vectors):
            score = _cosine(q, vec)
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict[str, Any]] = []
        for score, idx in scored[:top_k]:
            item = dict(self.chunks[idx])
            item["score"] = float(score)
            out.append(item)
        return out

    def save(self, path: Path | None = None) -> None:
        path = Path(path or VECTOR_INDEX_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump(
                {
                    "version": 1,
                    "backend": self.backend,
                    "model": self.model,
                    "chunks": self.chunks,
                    "vectors": self.vectors,
                },
                f,
            )

    @classmethod
    def load(cls, path: Path | None = None) -> "VectorIndex":
        path = Path(path or VECTOR_INDEX_PATH)
        with path.open("rb") as f:
            payload = pickle.load(f)
        obj = cls()
        obj.backend = payload.get("backend", "ollama")
        obj.model = payload.get("model", EMBED_MODEL)
        obj.chunks = payload["chunks"]
        obj.vectors = payload["vectors"]
        return obj


def rebuild_retrieval_index(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Build vector index when embeddings work; always keep TF-IDF as lexical fallback.
    Returns status dict.
    """
    status: dict[str, Any] = {
        "chunk_count": len(chunks),
        "vector": False,
        "tfidf": False,
        "error": "",
        "model": EMBED_MODEL,
    }
    if not chunks:
        for p in (VECTOR_INDEX_PATH, INDEX_PATH):
            if Path(p).exists():
                Path(p).unlink()
        return status

    # Lexical fallback always available
    TfidfIndex().fit(chunks).save(INDEX_PATH)
    status["tfidf"] = True

    try:
        VectorIndex().fit(chunks).save(VECTOR_INDEX_PATH)
        status["vector"] = True
    except EmbeddingError as exc:
        status["error"] = str(exc)
        if VECTOR_INDEX_PATH.exists():
            # Stale vector index would mismatch chunks — remove it
            VECTOR_INDEX_PATH.unlink()
    return status


def _chunk_key(hit: dict[str, Any]) -> str:
    return f"{hit.get('document_id','')}|{hit.get('chunk_index', hit.get('location',''))}|{hit.get('text','')[:80]}"


def _rrf_fuse(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    top_k: int,
    k: int = 60,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion over multiple ranked hit lists."""
    scores: dict[str, float] = {}
    best: dict[str, dict[str, Any]] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            key = _chunk_key(hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            prev = best.get(key)
            if prev is None or float(hit.get("score") or 0) > float(prev.get("score") or 0):
                best[key] = dict(hit)
    ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    out: list[dict[str, Any]] = []
    for key, fused in ordered[:top_k]:
        item = best[key]
        item["score"] = float(fused)
        out.append(item)
    return out


def search_retrieval(
    query: str,
    *,
    top_k: int = 6,
    prefer_vector: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """
    Search Memory. Returns (hits, backend) where backend is
    hybrid|vector|tfidf|none.

    When both indexes exist, fuse with RRF (lexical + semantic).
    """
    fetch_k = max(top_k * 3, 12)
    vector_hits: list[dict[str, Any]] = []
    tfidf_hits: list[dict[str, Any]] = []

    if prefer_vector and VECTOR_INDEX_PATH.exists():
        try:
            vector_hits = VectorIndex.load().search(query, top_k=fetch_k)
        except EmbeddingError:
            vector_hits = []
        except Exception:
            vector_hits = []

    if INDEX_PATH.exists():
        try:
            tfidf_hits = TfidfIndex.load(INDEX_PATH).search(query, top_k=fetch_k)
        except Exception:
            tfidf_hits = []

    if vector_hits and tfidf_hits:
        return _rrf_fuse([vector_hits, tfidf_hits], top_k=top_k), "hybrid"
    if vector_hits:
        return vector_hits[:top_k], "vector"
    if tfidf_hits:
        return tfidf_hits[:top_k], "tfidf"
    return [], "none"
