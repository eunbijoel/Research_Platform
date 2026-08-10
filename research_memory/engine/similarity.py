"""Similarity facade — Doc_Similarity (MiniLM + parsers + pHash) pipeline.

Public entry points keep the same names used by ``app.py``.
"""
from __future__ import annotations

from typing import Any

from research_memory.engine.document_preview import resolve_document_file
from research_memory.engine.docsim.pipeline import default_settings, run_analysis
from research_memory.kb.repository import KnowledgeRepository


def _settings_from_kwargs(
    *,
    threshold: float = 0.85,
    page_threshold: float | None = None,
    enable_images: bool = True,
    phash_distance: int = 8,
    min_image_width: int = 180,
    min_image_height: int = 120,
    max_results: int = 500,
    max_sentences: int = 5000,
    exclude_boilerplate: bool = True,
    build_page_screenshots: bool = True,
) -> dict[str, Any]:
    return default_settings(
        sentence_threshold=float(threshold),
        page_threshold=float(page_threshold if page_threshold is not None else 0.72),
        enable_image_analysis=bool(enable_images),
        phash_distance_threshold=int(phash_distance),
        min_image_width=int(min_image_width),
        min_image_height=int(min_image_height),
        max_results=int(max_results),
        max_sentences=int(max_sentences),
        exclude_boilerplate_patterns=bool(exclude_boilerplate),
        exclude_common_sentences=bool(exclude_boilerplate),
        build_page_screenshots=bool(build_page_screenshots),
    )


def _kb_file(repo: KnowledgeRepository, document_id: str) -> tuple[bytes, str] | None:
    doc = repo.get_document(document_id)
    if not doc or doc.get("status") != "ready":
        return None
    path = resolve_document_file(doc)
    if path is None:
        return None
    try:
        return path.read_bytes(), str(doc.get("filename") or path.name)
    except OSError:
        return None


def compare_uploads(
    files: list[tuple[bytes, str]],
    *,
    threshold: float = 0.85,
    page_threshold: float | None = None,
    enable_images: bool = True,
    phash_distance: int = 8,
    min_image_width: int = 180,
    min_image_height: int = 120,
    **_: Any,
) -> dict[str, Any]:
    settings = _settings_from_kwargs(
        threshold=threshold,
        page_threshold=page_threshold,
        enable_images=enable_images,
        phash_distance=phash_distance,
        min_image_width=min_image_width,
        min_image_height=min_image_height,
    )
    result = run_analysis(files, settings)
    result["mode"] = "upload_vs_upload"
    return result


def compare_upload_vs_kb(
    data: bytes,
    filename: str,
    *,
    repo: KnowledgeRepository | None = None,
    threshold: float = 0.85,
    page_threshold: float | None = None,
    project_id: str | None = None,
    enable_images: bool = True,
    phash_distance: int = 8,
    min_image_width: int = 180,
    min_image_height: int = 120,
    **_: Any,
) -> dict[str, Any]:
    repo = repo or KnowledgeRepository()
    files: list[tuple[bytes, str]] = [(data, filename)]
    missing: list[str] = []
    for doc in repo.list_documents():
        if doc.get("status") != "ready":
            continue
        if project_id and (doc.get("project_id") or "") != project_id:
            continue
        loaded = _kb_file(repo, doc["id"])
        if loaded is None:
            missing.append(str(doc.get("filename") or doc["id"]))
            continue
        files.append(loaded)

    if len(files) < 2:
        return {
            "ok": False,
            "error": (
                "비교할 Memory 원본 파일이 없습니다. "
                "문서의 stored_path(data/raw)가 있어야 Doc_Similarity 파서를 사용할 수 있습니다."
                + (f" 누락: {', '.join(missing[:5])}" if missing else "")
            ),
            "pairs": [],
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "stats": {},
            "log_entries": [],
        }

    settings = _settings_from_kwargs(
        threshold=threshold,
        page_threshold=page_threshold,
        enable_images=enable_images,
        phash_distance=phash_distance,
        min_image_width=min_image_width,
        min_image_height=min_image_height,
    )
    result = run_analysis(files, settings)
    result["mode"] = "upload_vs_kb"
    result["query_file"] = filename
    if missing:
        logs = list(result.get("log_entries") or [])
        for name in missing:
            logs.append(
                {
                    "file_name": name,
                    "status": "건너뜀",
                    "message": "stored_path 원본 없음",
                }
            )
        result["log_entries"] = logs
    # Keep only pairs that involve the query file (optional clarity)
    q = filename
    result["sentence_pairs"] = [
        p for p in (result.get("sentence_pairs") or []) if p.get("file_a") == q or p.get("file_b") == q
    ]
    result["page_pairs"] = [
        p for p in (result.get("page_pairs") or []) if p.get("file_a") == q or p.get("file_b") == q
    ]
    result["image_pairs"] = [
        p for p in (result.get("image_pairs") or []) if p.get("file_a") == q or p.get("file_b") == q
    ]
    result["pairs"] = result["sentence_pairs"]
    return result


def compare_kb_documents(
    document_id_a: str,
    document_id_b: str,
    *,
    repo: KnowledgeRepository | None = None,
    threshold: float = 0.85,
    page_threshold: float | None = None,
    enable_images: bool = True,
    phash_distance: int = 8,
    min_image_width: int = 180,
    min_image_height: int = 120,
    **_: Any,
) -> dict[str, Any]:
    repo = repo or KnowledgeRepository()
    a = _kb_file(repo, document_id_a)
    b = _kb_file(repo, document_id_b)
    if a is None or b is None:
        return {
            "ok": False,
            "error": "One or both documents missing original file (stored_path)",
            "pairs": [],
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "stats": {},
        }
    settings = _settings_from_kwargs(
        threshold=threshold,
        page_threshold=page_threshold,
        enable_images=enable_images,
        phash_distance=phash_distance,
        min_image_width=min_image_width,
        min_image_height=min_image_height,
    )
    result = run_analysis([a, b], settings)
    result["mode"] = "kb_vs_kb"
    result["file_a"] = a[1]
    result["file_b"] = b[1]
    return result
