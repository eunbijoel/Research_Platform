from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from research_memory.kb.index import TfidfIndex, tokenize
from research_memory.kb.repository import KnowledgeRepository
from research_memory.pipeline.chunking import refine_chunks
from research_memory.pipeline.extractors import extract_chunks
from research_memory.schema import TextChunk

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？\n])\s+|(?<=다\.)\s+|(?<=요\.)\s+")
_WS = re.compile(r"\s+")


@dataclass
class Unit:
    document_id: str
    filename: str
    location: str
    text: str
    normalized: str
    unit_id: str


@dataclass
class SimilarityPair:
    file_a: str
    location_a: str
    text_a: str
    document_id_a: str
    file_b: str
    location_b: str
    text_b: str
    document_id_b: str
    score: float
    verdict: str
    match_type: str  # exact | similar

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_text(text: str) -> str:
    return _WS.sub(" ", (text or "").strip().lower())


def verdict_for(score: float, *, exact: bool = False) -> str:
    if exact or score >= 0.98:
        return "exact"
    if score >= 0.85:
        return "high"
    if score >= 0.72:
        return "medium"
    return "low"


def units_from_chunks(
    chunks: Iterable[TextChunk | dict[str, Any]],
    *,
    document_id: str,
    filename: str,
    min_chars: int = 20,
) -> list[Unit]:
    units: list[Unit] = []
    for i, ch in enumerate(chunks):
        if isinstance(ch, TextChunk):
            text = ch.text
            location = ch.location
            idx = ch.chunk_index
        else:
            text = ch.get("text", "")
            location = ch.get("location", "")
            idx = ch.get("chunk_index", i)
        for j, sentence in enumerate(_split_sentences(text)):
            if len(sentence) < min_chars:
                continue
            norm = normalize_text(sentence)
            if len(norm) < min_chars:
                continue
            units.append(
                Unit(
                    document_id=document_id,
                    filename=filename,
                    location=f"{location} · s{j + 1}" if location else f"s{j + 1}",
                    text=sentence,
                    normalized=norm,
                    unit_id=f"{document_id}:{idx}:{j}",
                )
            )
    return units


def units_from_kb_document(repo: KnowledgeRepository, document_id: str) -> list[Unit]:
    doc = repo.get_document(document_id)
    if not doc or doc.get("status") != "ready":
        return []
    chunks = [c for c in repo.iter_chunks() if c["document_id"] == document_id]
    return units_from_chunks(
        chunks,
        document_id=document_id,
        filename=doc["filename"],
    )


def units_from_file_bytes(data: bytes, filename: str) -> tuple[list[Unit], str]:
    """Parse upload bytes into compare units. Returns (units, error)."""
    from hashlib import sha256
    from pathlib import Path
    import tempfile

    digest = sha256(data).hexdigest()[:12]
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    try:
        _ftype, raw, err = extract_chunks(path)
        if err and not raw:
            return [], err
        chunks = refine_chunks(raw)
        units = units_from_chunks(
            chunks,
            document_id=f"upload:{digest}",
            filename=filename,
        )
        if not units:
            return [], err or "No comparable text extracted"
        return units, ""
    finally:
        path.unlink(missing_ok=True)


def compare_unit_sets(
    side_a: list[Unit],
    side_b: list[Unit],
    *,
    threshold: float = 0.72,
    top_k_per_unit: int = 3,
    max_pairs: int = 200,
) -> list[SimilarityPair]:
    """Cross-compare two unit sets (exact first, then TF-IDF similar)."""
    if not side_a or not side_b:
        return []

    pairs: list[SimilarityPair] = []
    seen: set[tuple[str, str]] = set()

    # Exact normalized matches
    index_b: dict[str, list[Unit]] = {}
    for u in side_b:
        index_b.setdefault(u.normalized, []).append(u)
    for a in side_a:
        for b in index_b.get(a.normalized, []):
            key = _pair_key(a.unit_id, b.unit_id)
            if key in seen:
                continue
            if a.document_id == b.document_id and a.filename == b.filename:
                continue
            seen.add(key)
            pairs.append(
                SimilarityPair(
                    file_a=a.filename,
                    location_a=a.location,
                    text_a=a.text,
                    document_id_a=a.document_id,
                    file_b=b.filename,
                    location_b=b.location,
                    text_b=b.text,
                    document_id_b=b.document_id,
                    score=1.0,
                    verdict=verdict_for(1.0, exact=True),
                    match_type="exact",
                )
            )

    # TF-IDF nearest neighbors across sides
    all_units = side_a + side_b
    chunks_for_index = [
        {
            "text": u.text,
            "unit_id": u.unit_id,
            "document_id": u.document_id,
            "filename": u.filename,
            "location": u.location,
        }
        for u in all_units
    ]
    index = TfidfIndex().fit(chunks_for_index)
    a_ids = {u.unit_id for u in side_a}
    b_by_id = {u.unit_id: u for u in side_b}
    a_by_id = {u.unit_id: u for u in side_a}

    for a in side_a:
        hits = index.search(a.text, top_k=top_k_per_unit + 8)
        kept = 0
        for hit in hits:
            uid = hit.get("unit_id")
            if uid not in b_by_id:
                continue
            if uid in a_ids and hit.get("document_id") == a.document_id:
                continue
            score = float(hit.get("score") or 0.0)
            if score < threshold:
                continue
            b = b_by_id[uid]
            if not _has_lexical_support(a.text, b.text):
                continue
            key = _pair_key(a.unit_id, b.unit_id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(
                SimilarityPair(
                    file_a=a.filename,
                    location_a=a.location,
                    text_a=a.text,
                    document_id_a=a.document_id,
                    file_b=b.filename,
                    location_b=b.location,
                    text_b=b.text,
                    document_id_b=b.document_id,
                    score=score,
                    verdict=verdict_for(score),
                    match_type="similar",
                )
            )
            kept += 1
            if kept >= top_k_per_unit:
                break

    # Also search from B→A to catch asymmetric neighbors (deduped by seen)
    for b in side_b:
        hits = index.search(b.text, top_k=top_k_per_unit + 8)
        kept = 0
        for hit in hits:
            uid = hit.get("unit_id")
            if uid not in a_by_id:
                continue
            score = float(hit.get("score") or 0.0)
            if score < threshold:
                continue
            a = a_by_id[uid]
            if not _has_lexical_support(a.text, b.text):
                continue
            key = _pair_key(a.unit_id, b.unit_id)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(
                SimilarityPair(
                    file_a=a.filename,
                    location_a=a.location,
                    text_a=a.text,
                    document_id_a=a.document_id,
                    file_b=b.filename,
                    location_b=b.location,
                    text_b=b.text,
                    document_id_b=b.document_id,
                    score=score,
                    verdict=verdict_for(score),
                    match_type="similar",
                )
            )
            kept += 1
            if kept >= top_k_per_unit:
                break

    pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    return pairs[:max_pairs]


def compare_upload_vs_kb(
    data: bytes,
    filename: str,
    *,
    repo: KnowledgeRepository | None = None,
    threshold: float = 0.72,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Phase 2 primary mode: new asset vs organizational Memory."""
    repo = repo or KnowledgeRepository()
    side_a, err = units_from_file_bytes(data, filename)
    if err and not side_a:
        return {"ok": False, "error": err, "pairs": [], "stats": {}}

    kb_units: list[Unit] = []
    for doc in repo.list_documents():
        if doc.get("status") != "ready":
            continue
        if project_id and (doc.get("project_id") or "") != project_id:
            continue
        kb_units.extend(units_from_kb_document(repo, doc["id"]))

    pairs = compare_unit_sets(side_a, kb_units, threshold=threshold)
    return {
        "ok": True,
        "mode": "upload_vs_kb",
        "error": err,
        "query_file": filename,
        "query_units": len(side_a),
        "kb_units": len(kb_units),
        "pairs": [p.to_dict() for p in pairs],
        "stats": _stats(pairs),
    }


def compare_kb_documents(
    document_id_a: str,
    document_id_b: str,
    *,
    repo: KnowledgeRepository | None = None,
    threshold: float = 0.72,
) -> dict[str, Any]:
    repo = repo or KnowledgeRepository()
    side_a = units_from_kb_document(repo, document_id_a)
    side_b = units_from_kb_document(repo, document_id_b)
    if not side_a or not side_b:
        return {
            "ok": False,
            "error": "One or both documents missing/empty",
            "pairs": [],
            "stats": {},
        }
    pairs = compare_unit_sets(side_a, side_b, threshold=threshold)
    doc_a = repo.get_document(document_id_a) or {}
    doc_b = repo.get_document(document_id_b) or {}
    return {
        "ok": True,
        "mode": "kb_vs_kb",
        "file_a": doc_a.get("filename"),
        "file_b": doc_b.get("filename"),
        "query_units": len(side_a),
        "kb_units": len(side_b),
        "pairs": [p.to_dict() for p in pairs],
        "stats": _stats(pairs),
    }


def compare_uploads(
    files: list[tuple[bytes, str]],
    *,
    threshold: float = 0.72,
) -> dict[str, Any]:
    """Compare multiple uploads pairwise (Doc_Similarity-style, Memory-agnostic)."""
    if len(files) < 2:
        return {"ok": False, "error": "Need at least 2 files", "pairs": [], "stats": {}}
    unit_sets: list[list[Unit]] = []
    errors: list[str] = []
    for data, name in files:
        units, err = units_from_file_bytes(data, name)
        if err and not units:
            errors.append(f"{name}: {err}")
        unit_sets.append(units)
    pairs: list[SimilarityPair] = []
    for i in range(len(unit_sets)):
        for j in range(i + 1, len(unit_sets)):
            pairs.extend(compare_unit_sets(unit_sets[i], unit_sets[j], threshold=threshold))
    pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    return {
        "ok": True,
        "mode": "upload_vs_upload",
        "errors": errors,
        "pairs": [p.to_dict() for p in pairs[:200]],
        "stats": _stats(pairs[:200]),
    }


def _split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    if len(parts) <= 1 and len(text) > 400:
        # Fallback windows for long undivided blocks
        out = []
        step = 280
        for start in range(0, len(text), step):
            out.append(text[start : start + step].strip())
        return [p for p in out if p]
    return parts or [text]


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _has_lexical_support(a: str, b: str) -> bool:
    ta = set(tokenize(a))
    tb = set(tokenize(b))
    if not ta or not tb:
        return False
    inter = ta & tb
    if not inter:
        return False
    jacc = len(inter) / max(len(ta | tb), 1)
    short = min(len(a), len(b)) < 25
    return jacc >= (0.2 if short else 0.08)


def _stats(pairs: list[SimilarityPair] | list[dict[str, Any]]) -> dict[str, Any]:
    if not pairs:
        return {"pair_count": 0, "exact": 0, "high": 0, "medium": 0, "low": 0}
    as_dicts = [p.to_dict() if isinstance(p, SimilarityPair) else p for p in pairs]
    counts = Counter(p.get("verdict", "low") for p in as_dicts)
    return {
        "pair_count": len(as_dicts),
        "exact": counts.get("exact", 0),
        "high": counts.get("high", 0),
        "medium": counts.get("medium", 0),
        "low": counts.get("low", 0),
        "max_score": max(float(p.get("score", 0)) for p in as_dicts),
    }