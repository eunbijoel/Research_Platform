from __future__ import annotations

import io
import re
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from research_memory.engine.document_preview import resolve_document_file
from research_memory.kb.index import TfidfIndex, tokenize
from research_memory.kb.repository import KnowledgeRepository
from research_memory.pipeline.chunking import refine_chunks
from research_memory.pipeline.extractors import extract_chunks_from_bytes
from research_memory.schema import TextChunk

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？\n])\s+|(?<=다\.)\s+|(?<=요\.)\s+")
_WS = re.compile(r"\s+")
_PAGE_RE = re.compile(r"(\d+)\s*페이지")

DEFAULT_MIN_IMAGE_WIDTH = 180
DEFAULT_MIN_IMAGE_HEIGHT = 120
DEFAULT_PHASH_DISTANCE = 8
MAX_IMAGES_PER_DOC = 40
MAX_IMAGE_PAIRS = 80
MAX_PAGE_PAIRS = 100
MAX_SENTENCE_PAIRS = 200


@dataclass
class Unit:
    document_id: str
    filename: str
    location: str
    text: str
    normalized: str
    unit_id: str
    page: int | None = None


@dataclass
class ImageUnit:
    document_id: str
    filename: str
    location: str
    image_id: str
    width: int
    height: int
    image_bytes: bytes
    phash: str | None = None


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
    page_a: int | None = None
    page_b: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocBundle:
    document_id: str
    filename: str
    sentence_units: list[Unit] = field(default_factory=list)
    page_units: list[Unit] = field(default_factory=list)
    images: list[ImageUnit] = field(default_factory=list)
    error: str = ""


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


def image_verdict(distance: int) -> str:
    if distance <= 0:
        return "exact"
    if distance <= 5:
        return "high"
    if distance <= 8:
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
            page = ch.page
        else:
            text = ch.get("text", "")
            location = ch.get("location", "")
            idx = ch.get("chunk_index", i)
            page = ch.get("page")
        page_n = _coerce_page(page, location)
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
                    page=page_n,
                )
            )
    return units


def page_units_from_chunks(
    chunks: Iterable[TextChunk | dict[str, Any]],
    *,
    document_id: str,
    filename: str,
    min_chars: int = 40,
) -> list[Unit]:
    """Aggregate chunk text by page (or chunk location when page is unknown)."""
    buckets: dict[str, list[str]] = defaultdict(list)
    page_by_key: dict[str, int | None] = {}
    loc_labels: dict[str, str] = {}
    for i, ch in enumerate(chunks):
        if isinstance(ch, TextChunk):
            text = (ch.text or "").strip()
            location = ch.location or ""
            page = ch.page
            idx = ch.chunk_index
        else:
            text = str(ch.get("text") or "").strip()
            location = str(ch.get("location") or "")
            page = ch.get("page")
            idx = ch.get("chunk_index", i)
        if not text:
            continue
        page_n = _coerce_page(page, location)
        if page_n is not None:
            key = f"p{page_n}"
            loc = f"{page_n}페이지"
        else:
            key = f"loc:{location or idx}"
            loc = location or f"구간 {idx + 1}"
        buckets[key].append(text)
        page_by_key[key] = page_n
        loc_labels.setdefault(key, loc)

    units: list[Unit] = []
    for key, parts in buckets.items():
        text = _WS.sub(" ", "\n".join(parts)).strip()
        if len(text) < min_chars:
            continue
        units.append(
            Unit(
                document_id=document_id,
                filename=filename,
                location=loc_labels.get(key, key),
                text=text[:8000],
                normalized=normalize_text(text[:8000]),
                unit_id=f"{document_id}:page:{key}",
                page=page_by_key.get(key),
            )
        )
    units.sort(key=lambda u: (u.page is None, u.page or 0, u.location))
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


def page_units_from_kb_document(repo: KnowledgeRepository, document_id: str) -> list[Unit]:
    doc = repo.get_document(document_id)
    if not doc or doc.get("status") != "ready":
        return []
    chunks = [c for c in repo.iter_chunks() if c["document_id"] == document_id]
    return page_units_from_chunks(
        chunks,
        document_id=document_id,
        filename=doc["filename"],
    )


def units_from_file_bytes(data: bytes, filename: str) -> tuple[list[Unit], str]:
    """Parse upload bytes into compare units. Returns (units, error)."""
    digest = sha256(data).hexdigest()[:12]
    _ftype, raw, err = extract_chunks_from_bytes(data, filename)
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


def bundle_from_bytes(
    data: bytes,
    filename: str,
    *,
    enable_images: bool = True,
    min_image_width: int = DEFAULT_MIN_IMAGE_WIDTH,
    min_image_height: int = DEFAULT_MIN_IMAGE_HEIGHT,
) -> DocBundle:
    digest = sha256(data).hexdigest()[:12]
    doc_id = f"upload:{digest}"
    _ftype, raw, err = extract_chunks_from_bytes(data, filename)
    chunks = refine_chunks(raw) if raw else []
    sentences = units_from_chunks(chunks, document_id=doc_id, filename=filename)
    pages = page_units_from_chunks(chunks, document_id=doc_id, filename=filename)
    images: list[ImageUnit] = []
    if enable_images:
        images = extract_images_from_bytes(
            data,
            filename,
            document_id=doc_id,
            min_width=min_image_width,
            min_height=min_image_height,
        )
        for img in images:
            compute_phash(img)
    return DocBundle(
        document_id=doc_id,
        filename=filename,
        sentence_units=sentences,
        page_units=pages,
        images=images,
        error=err if (err and not sentences and not pages) else "",
    )


def bundle_from_kb(
    repo: KnowledgeRepository,
    document_id: str,
    *,
    enable_images: bool = True,
    min_image_width: int = DEFAULT_MIN_IMAGE_WIDTH,
    min_image_height: int = DEFAULT_MIN_IMAGE_HEIGHT,
) -> DocBundle:
    doc = repo.get_document(document_id)
    if not doc or doc.get("status") != "ready":
        return DocBundle(document_id=document_id, filename="", error="Document missing")
    filename = str(doc.get("filename") or document_id)
    chunks = [c for c in repo.iter_chunks() if c["document_id"] == document_id]
    sentences = units_from_chunks(chunks, document_id=document_id, filename=filename)
    pages = page_units_from_chunks(chunks, document_id=document_id, filename=filename)
    images: list[ImageUnit] = []
    if enable_images:
        path = resolve_document_file(doc)
        if path is not None:
            try:
                data = path.read_bytes()
                images = extract_images_from_bytes(
                    data,
                    filename,
                    document_id=document_id,
                    min_width=min_image_width,
                    min_height=min_image_height,
                )
                for img in images:
                    compute_phash(img)
            except OSError:
                pass
    return DocBundle(
        document_id=document_id,
        filename=filename,
        sentence_units=sentences,
        page_units=pages,
        images=images,
    )


def extract_images_from_bytes(
    data: bytes,
    filename: str,
    *,
    document_id: str,
    min_width: int = DEFAULT_MIN_IMAGE_WIDTH,
    min_height: int = DEFAULT_MIN_IMAGE_HEIGHT,
) -> list[ImageUnit]:
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _extract_pdf_images(
            data,
            filename,
            document_id=document_id,
            min_width=min_width,
            min_height=min_height,
        )
    if ext == ".docx":
        return _extract_docx_images(
            data,
            filename,
            document_id=document_id,
            min_width=min_width,
            min_height=min_height,
        )
    return []


def compute_phash(image: ImageUnit) -> ImageUnit:
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        return image
    try:
        pil = Image.open(io.BytesIO(image.image_bytes))
        image.phash = str(imagehash.phash(pil))
        if not image.width or not image.height:
            image.width, image.height = pil.size
    except Exception:  # noqa: BLE001
        image.phash = None
    return image


def compare_image_sets(
    side_a: list[ImageUnit],
    side_b: list[ImageUnit],
    *,
    distance_threshold: int = DEFAULT_PHASH_DISTANCE,
    max_pairs: int = MAX_IMAGE_PAIRS,
) -> list[dict[str, Any]]:
    a_valid = [i for i in side_a if i.phash]
    b_valid = [i for i in side_b if i.phash]
    if not a_valid or not b_valid:
        return []
    try:
        import imagehash
    except ImportError:
        return []

    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ia in a_valid:
        hash_a = imagehash.hex_to_hash(ia.phash)
        for ib in b_valid:
            if ia.document_id == ib.document_id and ia.filename == ib.filename:
                continue
            key = _pair_key(ia.image_id, ib.image_id)
            if key in seen:
                continue
            hash_b = imagehash.hex_to_hash(ib.phash)
            distance = int(hash_a - hash_b)
            if distance > distance_threshold:
                continue
            seen.add(key)
            pairs.append(
                {
                    "file_a": ia.filename,
                    "location_a": ia.location,
                    "image_id_a": ia.image_id,
                    "image_bytes_a": ia.image_bytes,
                    "width_a": ia.width,
                    "height_a": ia.height,
                    "file_b": ib.filename,
                    "location_b": ib.location,
                    "image_id_b": ib.image_id,
                    "image_bytes_b": ib.image_bytes,
                    "width_b": ib.width,
                    "height_b": ib.height,
                    "phash_distance": distance,
                    "score": max(0.0, 1.0 - distance / max(distance_threshold, 1)),
                    "verdict": image_verdict(distance),
                    "match_type": "exact" if distance == 0 else "similar",
                }
            )
    pairs.sort(key=lambda p: (p["phash_distance"], p["file_a"], p["file_b"]))
    return pairs[:max_pairs]


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
                    page_a=a.page,
                    page_b=b.page,
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
                    page_a=a.page,
                    page_b=b.page,
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
                    page_a=a.page,
                    page_b=b.page,
                )
            )
            kept += 1
            if kept >= top_k_per_unit:
                break

    pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    return pairs[:max_pairs]


def compare_bundles(
    bundles: list[DocBundle],
    *,
    threshold: float = 0.72,
    page_threshold: float | None = None,
    enable_images: bool = True,
    phash_distance: int = DEFAULT_PHASH_DISTANCE,
) -> dict[str, Any]:
    """Pairwise compare 2+ document bundles (sentence + page + image)."""
    page_th = threshold if page_threshold is None else page_threshold
    if len(bundles) < 2:
        return {
            "ok": False,
            "error": "Need at least 2 documents",
            "pairs": [],
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "stats": {},
        }

    sentence_pairs: list[SimilarityPair] = []
    page_pairs: list[SimilarityPair] = []
    image_pairs: list[dict[str, Any]] = []
    errors = [b.error for b in bundles if b.error]

    for i in range(len(bundles)):
        for j in range(i + 1, len(bundles)):
            a, b = bundles[i], bundles[j]
            sentence_pairs.extend(
                compare_unit_sets(
                    a.sentence_units,
                    b.sentence_units,
                    threshold=threshold,
                    max_pairs=MAX_SENTENCE_PAIRS,
                )
            )
            page_pairs.extend(
                compare_unit_sets(
                    a.page_units,
                    b.page_units,
                    threshold=page_th,
                    top_k_per_unit=2,
                    max_pairs=MAX_PAGE_PAIRS,
                )
            )
            if enable_images:
                image_pairs.extend(
                    compare_image_sets(
                        a.images,
                        b.images,
                        distance_threshold=phash_distance,
                    )
                )

    sentence_pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    page_pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    image_pairs.sort(key=lambda p: (p.get("phash_distance", 99), p.get("file_a"), p.get("file_b")))

    sentence_pairs = sentence_pairs[:MAX_SENTENCE_PAIRS]
    page_pairs = page_pairs[:MAX_PAGE_PAIRS]
    image_pairs = image_pairs[:MAX_IMAGE_PAIRS]

    sent_dicts = [p.to_dict() for p in sentence_pairs]
    page_dicts = [p.to_dict() for p in page_pairs]

    return {
        "ok": True,
        "errors": errors,
        "pairs": sent_dicts,  # back-compat
        "sentence_pairs": sent_dicts,
        "page_pairs": page_dicts,
        "image_pairs": image_pairs,
        "query_units": sum(len(b.sentence_units) for b in bundles),
        "page_units": sum(len(b.page_units) for b in bundles),
        "image_units": sum(len(b.images) for b in bundles),
        "stats": {
            "sentence": _stats(sentence_pairs),
            "page": _stats(page_pairs),
            "image": {
                "pair_count": len(image_pairs),
                "exact": sum(1 for p in image_pairs if p.get("verdict") == "exact"),
                "high": sum(1 for p in image_pairs if p.get("verdict") == "high"),
                "medium": sum(1 for p in image_pairs if p.get("verdict") == "medium"),
                "low": sum(1 for p in image_pairs if p.get("verdict") == "low"),
                "max_score": max((float(p.get("score") or 0) for p in image_pairs), default=0.0),
            },
            # flat aliases for older UI
            **_stats(sentence_pairs),
        },
        "file_names": [b.filename for b in bundles],
    }


def compare_upload_vs_kb(
    data: bytes,
    filename: str,
    *,
    repo: KnowledgeRepository | None = None,
    threshold: float = 0.72,
    page_threshold: float | None = None,
    project_id: str | None = None,
    enable_images: bool = True,
    phash_distance: int = DEFAULT_PHASH_DISTANCE,
    min_image_width: int = DEFAULT_MIN_IMAGE_WIDTH,
    min_image_height: int = DEFAULT_MIN_IMAGE_HEIGHT,
) -> dict[str, Any]:
    """Phase 2 primary mode: new asset vs organizational Memory."""
    repo = repo or KnowledgeRepository()
    query = bundle_from_bytes(
        data,
        filename,
        enable_images=enable_images,
        min_image_width=min_image_width,
        min_image_height=min_image_height,
    )
    if query.error and not query.sentence_units and not query.page_units:
        return {
            "ok": False,
            "error": query.error,
            "pairs": [],
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "stats": {},
        }

    kb_bundles: list[DocBundle] = []
    for doc in repo.list_documents():
        if doc.get("status") != "ready":
            continue
        if project_id and (doc.get("project_id") or "") != project_id:
            continue
        kb_bundles.append(
            bundle_from_kb(
                repo,
                doc["id"],
                enable_images=enable_images,
                min_image_width=min_image_width,
                min_image_height=min_image_height,
            )
        )

    # Compare query against merged KB sides one-by-one, then merge pairs
    sentence_pairs: list[SimilarityPair] = []
    page_pairs: list[SimilarityPair] = []
    image_pairs: list[dict[str, Any]] = []
    page_th = threshold if page_threshold is None else page_threshold
    kb_sent = 0
    kb_page = 0
    kb_img = 0
    for kb in kb_bundles:
        kb_sent += len(kb.sentence_units)
        kb_page += len(kb.page_units)
        kb_img += len(kb.images)
        sentence_pairs.extend(
            compare_unit_sets(
                query.sentence_units,
                kb.sentence_units,
                threshold=threshold,
                max_pairs=MAX_SENTENCE_PAIRS,
            )
        )
        page_pairs.extend(
            compare_unit_sets(
                query.page_units,
                kb.page_units,
                threshold=page_th,
                top_k_per_unit=2,
                max_pairs=MAX_PAGE_PAIRS,
            )
        )
        if enable_images:
            image_pairs.extend(
                compare_image_sets(
                    query.images,
                    kb.images,
                    distance_threshold=phash_distance,
                )
            )

    sentence_pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    page_pairs.sort(key=lambda p: (-p.score, p.file_a, p.file_b))
    image_pairs.sort(key=lambda p: (p.get("phash_distance", 99), p.get("file_a")))
    sentence_pairs = sentence_pairs[:MAX_SENTENCE_PAIRS]
    page_pairs = page_pairs[:MAX_PAGE_PAIRS]
    image_pairs = image_pairs[:MAX_IMAGE_PAIRS]
    sent_dicts = [p.to_dict() for p in sentence_pairs]
    page_dicts = [p.to_dict() for p in page_pairs]

    return {
        "ok": True,
        "mode": "upload_vs_kb",
        "error": query.error or None,
        "query_file": filename,
        "query_units": len(query.sentence_units),
        "kb_units": kb_sent,
        "page_units_query": len(query.page_units),
        "page_units_kb": kb_page,
        "image_units_query": len(query.images),
        "image_units_kb": kb_img,
        "pairs": sent_dicts,
        "sentence_pairs": sent_dicts,
        "page_pairs": page_dicts,
        "image_pairs": image_pairs,
        "stats": {
            "sentence": _stats(sentence_pairs),
            "page": _stats(page_pairs),
            "image": {
                "pair_count": len(image_pairs),
                "exact": sum(1 for p in image_pairs if p.get("verdict") == "exact"),
                "high": sum(1 for p in image_pairs if p.get("verdict") == "high"),
                "medium": sum(1 for p in image_pairs if p.get("verdict") == "medium"),
                "low": sum(1 for p in image_pairs if p.get("verdict") == "low"),
                "max_score": max((float(p.get("score") or 0) for p in image_pairs), default=0.0),
            },
            **_stats(sentence_pairs),
        },
    }


def compare_kb_documents(
    document_id_a: str,
    document_id_b: str,
    *,
    repo: KnowledgeRepository | None = None,
    threshold: float = 0.72,
    page_threshold: float | None = None,
    enable_images: bool = True,
    phash_distance: int = DEFAULT_PHASH_DISTANCE,
    min_image_width: int = DEFAULT_MIN_IMAGE_WIDTH,
    min_image_height: int = DEFAULT_MIN_IMAGE_HEIGHT,
) -> dict[str, Any]:
    repo = repo or KnowledgeRepository()
    a = bundle_from_kb(
        repo,
        document_id_a,
        enable_images=enable_images,
        min_image_width=min_image_width,
        min_image_height=min_image_height,
    )
    b = bundle_from_kb(
        repo,
        document_id_b,
        enable_images=enable_images,
        min_image_width=min_image_width,
        min_image_height=min_image_height,
    )
    if (not a.sentence_units and not a.page_units) or (
        not b.sentence_units and not b.page_units
    ):
        return {
            "ok": False,
            "error": "One or both documents missing/empty",
            "pairs": [],
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "stats": {},
        }
    result = compare_bundles(
        [a, b],
        threshold=threshold,
        page_threshold=page_threshold,
        enable_images=enable_images,
        phash_distance=phash_distance,
    )
    result["mode"] = "kb_vs_kb"
    result["file_a"] = a.filename
    result["file_b"] = b.filename
    result["query_units"] = len(a.sentence_units)
    result["kb_units"] = len(b.sentence_units)
    return result


def compare_uploads(
    files: list[tuple[bytes, str]],
    *,
    threshold: float = 0.72,
    page_threshold: float | None = None,
    enable_images: bool = True,
    phash_distance: int = DEFAULT_PHASH_DISTANCE,
    min_image_width: int = DEFAULT_MIN_IMAGE_WIDTH,
    min_image_height: int = DEFAULT_MIN_IMAGE_HEIGHT,
) -> dict[str, Any]:
    """Compare multiple uploads pairwise (sentence + page + image)."""
    if len(files) < 2:
        return {
            "ok": False,
            "error": "Need at least 2 files",
            "pairs": [],
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "stats": {},
        }
    bundles = [
        bundle_from_bytes(
            data,
            name,
            enable_images=enable_images,
            min_image_width=min_image_width,
            min_image_height=min_image_height,
        )
        for data, name in files
    ]
    result = compare_bundles(
        bundles,
        threshold=threshold,
        page_threshold=page_threshold,
        enable_images=enable_images,
        phash_distance=phash_distance,
    )
    result["mode"] = "upload_vs_upload"
    return result


def _extract_pdf_images(
    data: bytes,
    filename: str,
    *,
    document_id: str,
    min_width: int,
    min_height: int,
) -> list[ImageUnit]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out: list[ImageUnit] = []
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:  # noqa: BLE001
        return []
    try:
        for page_i in range(len(pdf)):
            if len(out) >= MAX_IMAGES_PER_DOC:
                break
            page = pdf[page_i]
            try:
                objects = list(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]))
            except Exception:  # noqa: BLE001
                try:
                    objects = [
                        o
                        for o in page.get_objects()
                        if o.__class__.__name__ == "PdfImage"
                    ]
                except Exception:  # noqa: BLE001
                    objects = []
            img_i = 0
            for obj in objects:
                if len(out) >= MAX_IMAGES_PER_DOC:
                    break
                if not hasattr(obj, "extract"):
                    continue
                try:
                    w, h = obj.get_px_size()
                except Exception:  # noqa: BLE001
                    w, h = 0, 0
                if w and h and (w < min_width or h < min_height):
                    continue
                buf = io.BytesIO()
                try:
                    obj.extract(buf)
                    img_bytes = buf.getvalue()
                except Exception:  # noqa: BLE001
                    try:
                        bitmap = obj.get_bitmap(render=True)
                        pil = bitmap.to_pil()
                        out_buf = io.BytesIO()
                        pil.save(out_buf, format="PNG")
                        img_bytes = out_buf.getvalue()
                        w, h = pil.size
                    except Exception:  # noqa: BLE001
                        continue
                if not img_bytes:
                    continue
                if w and h and (w < min_width or h < min_height):
                    continue
                img_i += 1
                out.append(
                    ImageUnit(
                        document_id=document_id,
                        filename=filename,
                        location=f"{page_i + 1}페이지 · 이미지 {img_i}",
                        image_id=f"{document_id}:p{page_i + 1}:img{img_i}",
                        width=int(w or 0),
                        height=int(h or 0),
                        image_bytes=img_bytes,
                    )
                )
            page.close()
    finally:
        pdf.close()
    return out


def _extract_docx_images(
    data: bytes,
    filename: str,
    *,
    document_id: str,
    min_width: int,
    min_height: int,
) -> list[ImageUnit]:
    try:
        from docx import Document
        from PIL import Image
    except ImportError:
        return []
    suffix = Path(filename).suffix or ".docx"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        path = Path(tmp.name)
    out: list[ImageUnit] = []
    try:
        doc = Document(str(path))
        idx = 0
        for rel in doc.part.rels.values():
            if len(out) >= MAX_IMAGES_PER_DOC:
                break
            reltype = str(getattr(rel, "reltype", "") or "")
            if "image" not in reltype.lower():
                continue
            try:
                img_bytes = rel.target_part.blob
            except Exception:  # noqa: BLE001
                continue
            if not img_bytes:
                continue
            try:
                pil = Image.open(io.BytesIO(img_bytes))
                w, h = pil.size
            except Exception:  # noqa: BLE001
                continue
            if w < min_width or h < min_height:
                continue
            idx += 1
            out.append(
                ImageUnit(
                    document_id=document_id,
                    filename=filename,
                    location=f"이미지 {idx}",
                    image_id=f"{document_id}:docx:img{idx}",
                    width=int(w),
                    height=int(h),
                    image_bytes=img_bytes,
                )
            )
    except Exception:  # noqa: BLE001
        return out
    finally:
        path.unlink(missing_ok=True)
    return out


def _coerce_page(page: Any, location: str) -> int | None:
    if page is not None:
        try:
            return int(page)
        except (TypeError, ValueError):
            pass
    m = _PAGE_RE.search(str(location or ""))
    if m:
        return int(m.group(1))
    return None


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
        return {"pair_count": 0, "exact": 0, "high": 0, "medium": 0, "low": 0, "max_score": 0.0}
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
