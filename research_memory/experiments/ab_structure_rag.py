"""A/B: legacy vs structure chunking with identical Hybrid RRF.

Writes only under data/experiments/structure_rag/ — never touches production KB indexes.
Usage:
  .venv312/bin/python -m research_memory.experiments.ab_structure_rag
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_memory.config import PROJECT_ROOT
from research_memory.kb.embeddings import EmbeddingError, VectorIndex, _rrf_fuse
from research_memory.kb.index import TfidfIndex
from research_memory.pipeline.structure_rag import ChunkStrategy, build_chunks_for_strategy

EXP_ROOT = PROJECT_ROOT / "data" / "experiments" / "structure_rag"
RAW_DIR = PROJECT_ROOT / "data" / "raw"


@dataclass(frozen=True)
class EvalCase:
    query: str
    gold_doc_substrings: tuple[str, ...]
    gold_section_substrings: tuple[str, ...]
    note: str = ""


# Fixed probe set: DOCX KPI docs + one regulation PDF (structure-heavy).
EVAL_DOCS = (
    "6번, 예측정확도.docx",
    "8번 측정경과 예측 정확도.docx",
    "12번 지식베이스 연계 정확도.docx",
    "★산업기술혁신사업_공통_운영요령고시_제2024_218호.pdf",
)

EVAL_CASES: list[EvalCase] = [
    EvalCase(
        query="예측정확도 평가 방법은 무엇인가?",
        gold_doc_substrings=("예측정확도",),
        gold_section_substrings=("예측", "정확", "평가"),
        note="DOCX 지표 정의",
    ),
    EvalCase(
        query="측정경과 예측 정확도는 어떻게 계산하나?",
        gold_doc_substrings=("측정경과",),
        gold_section_substrings=("측정", "예측", "정확", "오차"),
        note="DOCX 계산식/정의",
    ),
    EvalCase(
        query="지식베이스 연계 정확도 평가 기준이 뭐야?",
        gold_doc_substrings=("지식베이스",),
        gold_section_substrings=("지식", "연계", "정확", "평가"),
        note="DOCX KB 연계",
    ),
    EvalCase(
        query="산업기술혁신사업 공통 운영요령에서 연구개발기관의 의무는?",
        gold_doc_substrings=("운영요령", "산업기술혁신"),
        gold_section_substrings=("연구개발", "기관", "의무", "제"),
        note="PDF 조항/제목",
    ),
    EvalCase(
        query="운영요령고시에서 협약과 관련된 내용은?",
        gold_doc_substrings=("운영요령",),
        gold_section_substrings=("협약", "제"),
        note="PDF 섹션 키워드",
    ),
    EvalCase(
        query="상대 오차율로 예측정확도를 어떻게 도출하나?",
        gold_doc_substrings=("예측", "측정경과"),
        gold_section_substrings=("오차", "정확", "평균"),
        note="표/수치 근거 가능성",
    ),
]


def _doc_id(name: str) -> str:
    safe = re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("_")
    return f"exp_{safe[:80]}"


def _resolve_docs() -> list[Path]:
    found: list[Path] = []
    for name in EVAL_DOCS:
        path = RAW_DIR / name
        if path.is_file():
            found.append(path)
        else:
            print(f"[skip missing] {name}")
    if not found:
        raise SystemExit(f"no eval docs under {RAW_DIR}")
    return found


def _build_index_rows(docs: list[Path], strategy: ChunkStrategy) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in docs:
        did = _doc_id(path.name)
        chunks = build_chunks_for_strategy(
            path, strategy=strategy, document_id=did, filename=path.name
        )
        rows.extend(ch.to_index_row() for ch in chunks)
    return rows


def _fit_indexes(rows: list[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {"chunk_count": len(rows), "tfidf": False, "vector": False, "error": ""}
    tfidf_path = out_dir / "tfidf_index.pkl"
    vector_path = out_dir / "vector_index.pkl"
    meta_path = out_dir / "chunks.jsonl"

    with meta_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    TfidfIndex().fit(rows).save(tfidf_path)
    status["tfidf"] = True
    try:
        VectorIndex().fit(rows).save(vector_path)
        status["vector"] = True
    except EmbeddingError as exc:
        status["error"] = str(exc)
        if vector_path.exists():
            vector_path.unlink()
    (out_dir / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return status


def _hybrid_search(query: str, index_dir: Path, *, top_k: int = 5) -> list[dict[str, Any]]:
    """Same fusion policy as production search_retrieval (tfidf weight 2.0, vector 1.0)."""
    fetch_k = max(top_k * 3, 12)
    tfidf_path = index_dir / "tfidf_index.pkl"
    vector_path = index_dir / "vector_index.pkl"
    vector_hits: list[dict[str, Any]] = []
    tfidf_hits: list[dict[str, Any]] = []
    if vector_path.exists():
        try:
            vector_hits = VectorIndex.load(vector_path).search(query, top_k=fetch_k)
        except Exception:
            vector_hits = []
    if tfidf_path.exists():
        try:
            tfidf_hits = TfidfIndex.load(tfidf_path).search(query, top_k=fetch_k)
        except Exception:
            tfidf_hits = []
    if vector_hits and tfidf_hits:
        return _rrf_fuse([tfidf_hits, vector_hits], top_k=top_k, weights=[2.0, 1.0])
    if vector_hits:
        return vector_hits[:top_k]
    if tfidf_hits:
        return tfidf_hits[:top_k]
    return []


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _doc_hit(hits: list[dict[str, Any]], gold_subs: tuple[str, ...]) -> bool:
    gold = tuple(_norm_key(g) for g in gold_subs)
    for h in hits:
        name = _norm_key(str(h.get("filename") or ""))
        if any(g and g in name for g in gold):
            return True
    return False


def _section_hit(hits: list[dict[str, Any]], gold_subs: tuple[str, ...]) -> bool:
    if not gold_subs:
        return False
    gold = tuple(_norm_key(g) for g in gold_subs)
    for h in hits[:5]:
        blob = _norm_key(
            " ".join(
                [
                    str(h.get("heading_path") or ""),
                    str(h.get("location") or ""),
                    str(h.get("text") or "")[:240],
                ]
            )
        )
        if any(g and g in blob for g in gold):
            return True
    return False


def _evidence_fit(hits: list[dict[str, Any]], gold_doc: tuple[str, ...], gold_sec: tuple[str, ...]) -> str:
    """Coarse manual-aid label: good / partial / weak."""
    if not hits:
        return "weak"
    top = hits[0]
    name = _norm_key(str(top.get("filename") or ""))
    blob = _norm_key(
        f"{top.get('heading_path','')} {top.get('location','')} {str(top.get('text') or '')[:300]}"
    )
    doc_ok = any(g and g in name for g in (_norm_key(x) for x in gold_doc))
    sec_ok = any(g and g in blob for g in (_norm_key(x) for x in gold_sec)) if gold_sec else False
    if doc_ok and sec_ok:
        return "good"
    if doc_ok or sec_ok:
        return "partial"
    return "weak"


def run() -> int:
    docs = _resolve_docs()
    print(f"experiment root: {EXP_ROOT}")
    print(f"docs ({len(docs)}): {[p.name for p in docs]}")

    statuses: dict[str, Any] = {}
    for strategy in ("legacy", "structure"):
        rows = _build_index_rows(docs, strategy)  # type: ignore[arg-type]
        out_dir = EXP_ROOT / strategy
        status = _fit_indexes(rows, out_dir)
        statuses[strategy] = status
        print(f"[{strategy}] chunks={status['chunk_count']} tfidf={status['tfidf']} vector={status['vector']} err={status['error']!r}")

    report_rows: list[dict[str, Any]] = []
    metrics = {
        "legacy": {"doc": 0, "sec": 0, "good": 0, "partial": 0, "weak": 0},
        "structure": {"doc": 0, "sec": 0, "good": 0, "partial": 0, "weak": 0},
    }
    n = len(EVAL_CASES)

    for case in EVAL_CASES:
        row: dict[str, Any] = {"query": case.query, "note": case.note}
        for strategy in ("legacy", "structure"):
            hits = _hybrid_search(case.query, EXP_ROOT / strategy, top_k=5)
            doc_ok = _doc_hit(hits, case.gold_doc_substrings)
            sec_ok = _section_hit(hits, case.gold_section_substrings)
            fit = _evidence_fit(hits, case.gold_doc_substrings, case.gold_section_substrings)
            metrics[strategy]["doc"] += int(doc_ok)
            metrics[strategy]["sec"] += int(sec_ok)
            metrics[strategy][fit] += 1
            top = hits[0] if hits else {}
            row[strategy] = {
                "doc_recall@5": doc_ok,
                "section_recall@5": sec_ok,
                "evidence_fit": fit,
                "top_file": top.get("filename"),
                "top_location": top.get("location"),
                "top_heading": top.get("heading_path"),
                "top_snippet": (top.get("text") or "")[:160],
            }
        report_rows.append(row)

    summary = {
        "n_queries": n,
        "legacy": {
            "document_recall@5": metrics["legacy"]["doc"] / n,
            "section_recall@5": metrics["legacy"]["sec"] / n,
            "evidence_good": metrics["legacy"]["good"],
            "evidence_partial": metrics["legacy"]["partial"],
            "evidence_weak": metrics["legacy"]["weak"],
            "chunks": statuses["legacy"]["chunk_count"],
        },
        "structure": {
            "document_recall@5": metrics["structure"]["doc"] / n,
            "section_recall@5": metrics["structure"]["sec"] / n,
            "evidence_good": metrics["structure"]["good"],
            "evidence_partial": metrics["structure"]["partial"],
            "evidence_weak": metrics["structure"]["weak"],
            "chunks": statuses["structure"]["chunk_count"],
        },
        "index_status": statuses,
        "cases": report_rows,
    }

    EXP_ROOT.mkdir(parents=True, exist_ok=True)
    out_json = EXP_ROOT / "ab_report.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(summary)
    print(f"\nWrote {out_json}")
    return 0


def _print_report(summary: dict[str, Any]) -> None:
    print("\n=== A/B Summary (identical Hybrid RRF; no heading boost) ===")
    for strategy in ("legacy", "structure"):
        s = summary[strategy]
        print(
            f"{strategy:10s}  DocRecall@5={s['document_recall@5']:.2f}  "
            f"SecRecall@5={s['section_recall@5']:.2f}  "
            f"evidence good/partial/weak={s['evidence_good']}/{s['evidence_partial']}/{s['evidence_weak']}  "
            f"chunks={s['chunks']}"
        )
    print("\n=== Per-query ===")
    for case in summary["cases"]:
        print(f"\nQ: {case['query']}")
        for strategy in ("legacy", "structure"):
            r = case[strategy]
            mark_d = "Y" if r["doc_recall@5"] else "N"
            mark_s = "Y" if r["section_recall@5"] else "N"
            print(
                f"  [{strategy:9s}] doc@{mark_d} sec@{mark_s} fit={r['evidence_fit']:7s} "
                f"| {r.get('top_file')} | {r.get('top_heading') or r.get('top_location')}"
            )


if __name__ == "__main__":
    raise SystemExit(run())
