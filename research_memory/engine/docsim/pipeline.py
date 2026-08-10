"""Doc_Similarity-style analysis pipeline without Streamlit."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Callable

from research_memory.engine.docsim.analyzers.image_similarity import (
    compute_phash,
    find_similar_image_pairs,
)
from research_memory.engine.docsim.analyzers.text_similarity import (
    find_exact_duplicate_pairs,
    find_similar_page_pairs,
    find_similar_sentence_pairs,
    load_model,
)
from research_memory.engine.docsim.parsers.dispatch import parse_document
from research_memory.engine.docsim.utils.boilerplate_filter import filter_boilerplate_sentences
from research_memory.engine.docsim.utils.config import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_MAX_SENTENCES,
    DEFAULT_MIN_IMAGE_HEIGHT,
    DEFAULT_MIN_IMAGE_WIDTH,
    DEFAULT_MIN_SENTENCE_LENGTH,
    DEFAULT_PAGE_THRESHOLD,
    DEFAULT_PHASH_DISTANCE_THRESHOLD,
    DEFAULT_SENTENCE_THRESHOLD,
    SENTENCE_MODEL_NAME,
)
from research_memory.engine.docsim.utils.page_screenshots import (
    build_matched_page_screenshots,
    collect_matched_page_pair_details,
)
from research_memory.engine.docsim.utils.summary_stats import (
    compute_file_pair_matrix,
    compute_sentence_overlap_stats,
    compute_similarity_distribution,
)


ProgressFn = Callable[[str, float], None] | None


@lru_cache(maxsize=1)
def get_embedding_model(model_name: str = SENTENCE_MODEL_NAME):
    return load_model(model_name)


def default_settings(**overrides: Any) -> dict[str, Any]:
    base = {
        "sentence_threshold": DEFAULT_SENTENCE_THRESHOLD,
        "page_threshold": DEFAULT_PAGE_THRESHOLD,
        "min_sentence_length": DEFAULT_MIN_SENTENCE_LENGTH,
        "max_sentences": DEFAULT_MAX_SENTENCES,
        "max_results": DEFAULT_MAX_RESULTS,
        "include_same_file": False,
        "exclude_boilerplate_patterns": True,
        "exclude_common_sentences": True,
        "common_min_file_count": 2,
        "enable_image_analysis": True,
        "min_image_width": DEFAULT_MIN_IMAGE_WIDTH,
        "min_image_height": DEFAULT_MIN_IMAGE_HEIGHT,
        "phash_distance_threshold": DEFAULT_PHASH_DISTANCE_THRESHOLD,
        "build_page_screenshots": True,
        "model_name": SENTENCE_MODEL_NAME,
    }
    base.update(overrides)
    return base


def run_analysis(
    files: list[tuple[bytes, str]],
    settings: dict[str, Any] | None = None,
    *,
    progress: ProgressFn = None,
) -> dict[str, Any]:
    """Parse and compare documents. ``files`` is ``[(bytes, filename), ...]``."""
    settings = default_settings(**(settings or {}))
    if len(files) < 2:
        return {
            "ok": False,
            "error": "Need at least 2 files",
            "sentence_pairs": [],
            "page_pairs": [],
            "image_pairs": [],
            "pairs": [],
            "stats": {},
            "log_entries": [],
        }

    def _prog(msg: str, frac: float) -> None:
        if progress:
            progress(msg, frac)

    log_entries: list[dict[str, Any]] = []
    all_sentences = []
    all_images = []
    all_pages = []
    pdf_bytes_by_name: dict[str, bytes] = {}
    file_names: list[str] = []

    total = len(files)
    for idx, (file_bytes, file_name) in enumerate(files):
        _prog(f"파싱: {file_name}", idx / max(total, 1) * 0.35)
        file_names.append(file_name)
        pdf_bytes_by_name[file_name] = file_bytes
        try:
            result = parse_document(
                file_name,
                file_bytes,
                min_sentence_length=int(settings["min_sentence_length"]),
                min_image_width=int(settings["min_image_width"]),
                min_image_height=int(settings["min_image_height"]),
            )
            if result.success:
                all_sentences.extend(result.sentences)
                all_images.extend(result.images)
                all_pages.extend(result.pages)
                log_entries.append(
                    {
                        "file_name": file_name,
                        "status": "성공",
                        "message": (
                            f"페이지 {len(result.pages)} · 문장 {len(result.sentences)} · "
                            f"이미지 {len(result.images)}"
                        ),
                    }
                )
            else:
                log_entries.append(
                    {
                        "file_name": file_name,
                        "status": "실패",
                        "message": result.error_message or "parse failed",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log_entries.append(
                {
                    "file_name": file_name,
                    "status": "실패",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )

    # Boilerplate filter
    boilerplate_stats = {
        "input": len(all_sentences),
        "removed_total": 0,
        "kept": len(all_sentences),
    }
    excluded_sentences: list[dict] = []
    if settings.get("exclude_boilerplate_patterns") or settings.get("exclude_common_sentences"):
        _prog("양식/공통 문장 필터링", 0.38)
        all_sentences, boilerplate_stats, excluded_sentences = filter_boilerplate_sentences(
            all_sentences,
            n_files=len(file_names),
            use_patterns=bool(settings.get("exclude_boilerplate_patterns", True)),
            use_common_across_files=bool(settings.get("exclude_common_sentences", True)),
        )

    truncated = False
    max_sentences = int(settings["max_sentences"])
    if len(all_sentences) > max_sentences:
        truncated = True
        all_sentences = all_sentences[:max_sentences]

    page_pairs: list[dict] = []
    sentence_pairs: list[dict] = []
    model = None
    model_error = ""
    try:
        _prog("임베딩 모델 로드", 0.42)
        model = get_embedding_model(str(settings.get("model_name") or SENTENCE_MODEL_NAME))
    except Exception as exc:  # noqa: BLE001
        model_error = f"{type(exc).__name__}: {exc}"

    if model is not None and len(all_pages) >= 2:
        _prog("유사 페이지 분석", 0.55)
        try:
            page_pairs = find_similar_page_pairs(
                all_pages,
                model,
                threshold=float(settings["page_threshold"]),
                include_same_file=bool(settings.get("include_same_file", False)),
            )
            page_pairs = page_pairs[: int(settings["max_results"])]
        except Exception as exc:  # noqa: BLE001
            log_entries.append(
                {"file_name": "(pages)", "status": "경고", "message": f"페이지 유사도: {exc}"}
            )

    if model is not None and len(all_sentences) >= 2:
        _prog("유사 문장 분석", 0.7)
        try:
            exact_pairs, exact_keys = find_exact_duplicate_pairs(
                all_sentences,
                include_same_file=bool(settings.get("include_same_file", False)),
            )
            sentence_pairs.extend(exact_pairs)
            similar_pairs = find_similar_sentence_pairs(
                all_sentences,
                model,
                threshold=float(settings["sentence_threshold"]),
                include_same_file=bool(settings.get("include_same_file", False)),
                already_found=exact_keys,
            )
            sentence_pairs.extend(similar_pairs)
        except Exception as exc:  # noqa: BLE001
            log_entries.append(
                {"file_name": "(sentences)", "status": "경고", "message": f"문장 유사도: {exc}"}
            )
        sentence_pairs.sort(key=lambda p: p["similarity"], reverse=True)
        sentence_pairs = sentence_pairs[: int(settings["max_results"])]

    image_pairs: list[dict] = []
    if settings.get("enable_image_analysis") and len(all_images) >= 2:
        _prog("이미지 유사도 분석", 0.82)
        for img in all_images:
            compute_phash(img)
        image_pairs = find_similar_image_pairs(
            all_images,
            distance_threshold=int(settings["phash_distance_threshold"]),
            include_same_file=bool(settings.get("include_same_file", False)),
        )
        image_pairs.sort(key=lambda p: p["phash_distance"])
        image_pairs = image_pairs[: int(settings["max_results"])]

    matched_page_pngs: list[dict] = []
    if settings.get("build_page_screenshots"):
        _prog("매칭 페이지 PNG", 0.9)
        try:
            details = collect_matched_page_pair_details(sentence_pairs)
            matched_page_pngs = build_matched_page_screenshots(
                pdf_bytes_by_name, details, dpi=120
            )
        except Exception as exc:  # noqa: BLE001
            log_entries.append(
                {"file_name": "(png)", "status": "경고", "message": f"페이지 PNG: {exc}"}
            )

    _prog("완료", 1.0)
    overlap = compute_sentence_overlap_stats(all_sentences, sentence_pairs)
    sim_dist = compute_similarity_distribution(sentence_pairs)
    file_matrix = compute_file_pair_matrix(sentence_pairs, file_names)

    # Normalize pair keys for Research Memory UI (score alias)
    sentence_ui = [_normalize_text_pair(p) for p in sentence_pairs]
    page_ui = [_normalize_text_pair(p) for p in page_pairs]
    image_ui = [_normalize_image_pair(p) for p in image_pairs]

    return {
        "ok": True,
        "error": model_error or None,
        "mode": "docsim",
        "file_names": file_names,
        "log_entries": log_entries,
        "sentences": all_sentences,
        "pages": all_pages,
        "images": all_images,
        "sentence_pairs": sentence_ui,
        "page_pairs": page_ui,
        "image_pairs": image_ui,
        "pairs": sentence_ui,
        "matched_page_pngs": matched_page_pngs,
        "overlap_stats": overlap,
        "similarity_distribution": sim_dist,
        "file_matrix": file_matrix,
        "boilerplate_stats": boilerplate_stats,
        "excluded_sentences": excluded_sentences,
        "sentences_truncated": truncated,
        "query_units": len(all_sentences),
        "page_units": len(all_pages),
        "image_units": len(all_images),
        "stats": {
            "sentence": _pair_stats(sentence_ui, score_key="score"),
            "page": _pair_stats(page_ui, score_key="score"),
            "image": _image_stats(image_ui),
            "pair_count": len(sentence_ui),
            "exact": sum(1 for p in sentence_ui if p.get("verdict") in {"exact", "동일 문장"}),
            "high": sum(1 for p in sentence_ui if "매우" in str(p.get("verdict") or "")),
            "medium": sum(1 for p in sentence_ui if p.get("verdict") not in {None, ""}),
            "max_score": max((float(p.get("score") or 0) for p in sentence_ui), default=0.0),
        },
    }


def _normalize_text_pair(pair: dict[str, Any]) -> dict[str, Any]:
    out = dict(pair)
    if "score" not in out and "similarity" in out:
        out["score"] = float(out["similarity"])
    # Map Korean verdicts loosely for UI helper
    verdict = str(out.get("verdict") or "")
    if verdict in {"동일 문장", "동일"}:
        out["match_type"] = "exact"
    else:
        out["match_type"] = out.get("match_type") or "similar"
    return out


def _normalize_image_pair(pair: dict[str, Any]) -> dict[str, Any]:
    out = dict(pair)
    dist = int(out.get("phash_distance") or 0)
    out["score"] = max(0.0, 1.0 - dist / 8.0)
    verdict = str(out.get("verdict") or "")
    if "동일" in verdict:
        out["match_type"] = "exact"
    else:
        out["match_type"] = "similar"
    # map for existing Korean label helper if needed
    if "동일" in verdict:
        out["verdict"] = "exact"
    elif "매우" in verdict:
        out["verdict"] = "high"
    elif "유사" in verdict:
        out["verdict"] = "medium"
    return out


def _pair_stats(pairs: list[dict[str, Any]], *, score_key: str) -> dict[str, Any]:
    if not pairs:
        return {"pair_count": 0, "exact": 0, "high": 0, "medium": 0, "low": 0, "max_score": 0.0}
    return {
        "pair_count": len(pairs),
        "exact": sum(1 for p in pairs if p.get("match_type") == "exact" or p.get("verdict") == "exact"),
        "high": sum(1 for p in pairs if p.get("verdict") == "high"),
        "medium": sum(1 for p in pairs if p.get("verdict") == "medium"),
        "low": sum(1 for p in pairs if p.get("verdict") == "low"),
        "max_score": max(float(p.get(score_key) or 0) for p in pairs),
    }


def _image_stats(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if not pairs:
        return {"pair_count": 0, "exact": 0, "high": 0, "medium": 0, "low": 0, "max_score": 0.0}
    return {
        "pair_count": len(pairs),
        "exact": sum(1 for p in pairs if p.get("verdict") == "exact"),
        "high": sum(1 for p in pairs if p.get("verdict") == "high"),
        "medium": sum(1 for p in pairs if p.get("verdict") == "medium"),
        "low": sum(1 for p in pairs if p.get("verdict") == "low"),
        "max_score": max(float(p.get("score") or 0) for p in pairs),
    }
