from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_memory.config import PROJECT_ROOT, ensure_data_dirs
from research_memory.engine.chat import answer_question
from research_memory.engine.similarity import compare_kb_documents, compare_upload_vs_kb
from research_memory.kb.repository import KnowledgeRepository
from research_memory.pipeline.ingest import ingest_file


def cmd_ingest(args: argparse.Namespace) -> None:
    ensure_data_dirs()
    repo = KnowledgeRepository()
    path = Path(args.path)
    if path.is_dir():
        files = sorted(
            p
            for p in path.rglob("*")
            if p.is_file()
            and p.suffix.lower()
            in {".pdf", ".docx", ".txt", ".md", ".csv", ".xlsx", ".xls", ".hwpx"}
        )
    else:
        files = [path]
    for f in files:
        result = ingest_file(f, repo=repo, project_id=args.project or "")
        print(json.dumps(result, ensure_ascii=False))


def cmd_chat(args: argparse.Namespace) -> None:
    result = answer_question(args.question)
    print(result.answer)
    print("---")
    for i, c in enumerate(result.citations, start=1):
        print(f"[{i}] {c.filename} / {c.location} ({c.score:.3f})")


def cmd_list(_: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    for d in repo.list_documents():
        print(
            f"{d['id'][:8]}  {d['status']:6}  chunks={d.get('chunk_count', 0):3}  {d['filename']}"
        )


def cmd_seed_demo(_: argparse.Namespace) -> None:
    ensure_data_dirs()
    demo_dir = PROJECT_ROOT / "demo"
    demo_dir.mkdir(exist_ok=True)
    samples = {
        "center_overview.md": """# KETI 소프트웨어센터 연구 개요 (데모)

과제명: 산업 데이터 스페이스 연계형 Research Memory
작성자: 조은비
연도: 2026

## 목적
센터가 축적한 제안서, 연구노트, 논문, 회의록을 근거 기반으로 재사용하는
Research Memory Platform을 구축한다.

## 핵심 원칙
1. Evidence-based reuse of organizational research assets
2. Preserve and operationalize institutional research knowledge
3. Assistant 기능이 아니라 Memory가 제품의 핵심이다.

## 예산 메모
1차 연도 예산: 120,000,000원
""",
        "proposal_excerpt.md": """# 제안서 발췌 (데모)

과제명: Manufacturing-X 연계 문서 지능
작성자: 소프트웨어센터
2025

## 센터 역할
본 센터는 HWP/PDF 문서 파싱, 유사도 분석, 제안서 초안 생성 도구를
통합하여 Research Memory Engine(Retrieval, Reasoning, Generation, Tracking)을 제공한다.

## 산출물
- Document Intelligence Pipeline
- Metadata / Facts 계층
- Knowledge Base
- AI Services: Chat, Similarity, Proposal, Milestone
""",
        "meeting_note.md": """# 회의록 (데모)

작성자: 연구지원
일시: 2026-07-15

## 안건
Research Memory Phase 1 범위 확정

## 결정
- Phase 1은 Chat(인용 강제)까지
- Catena-X / KMX는 별 제품 라인
- 기술 스택 세부는 Appendix로 보고
""",
    }
    repo = KnowledgeRepository()
    for name, body in samples.items():
        path = demo_dir / name
        path.write_text(body, encoding="utf-8")
        result = ingest_file(path, repo=repo, project_id="DEMO-2026")
        print(json.dumps(result, ensure_ascii=False))


def cmd_similarity(args: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    if args.doc_a and args.doc_b:
        result = compare_kb_documents(
            args.doc_a, args.doc_b, repo=repo, threshold=args.threshold
        )
    else:
        path = Path(args.path)
        result = compare_upload_vs_kb(
            path.read_bytes(),
            path.name,
            repo=repo,
            threshold=args.threshold,
            project_id=args.project or None,
        )
    print(json.dumps({"stats": result.get("stats"), "ok": result.get("ok"), "error": result.get("error"), "mode": result.get("mode")}, ensure_ascii=False))
    for i, p in enumerate(result.get("pairs") or [], start=1):
        print(
            f"[{i}] {p['verdict']} {p['score']:.3f} | "
            f"{p['file_a']}/{p['location_a']} ↔ {p['file_b']}/{p['location_b']}"
        )
        print(f"    A: {p['text_a'][:120]}")
        print(f"    B: {p['text_b'][:120]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="research-memory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="Ingest a file or directory")
    p_ing.add_argument("path")
    p_ing.add_argument("--project", default="")
    p_ing.set_defaults(func=cmd_ingest)

    p_chat = sub.add_parser("chat", help="Ask Research Memory")
    p_chat.add_argument("question")
    p_chat.set_defaults(func=cmd_chat)

    p_list = sub.add_parser("list", help="List KB documents")
    p_list.set_defaults(func=cmd_list)

    p_seed = sub.add_parser("seed_demo", help="Create and ingest demo corpus")
    p_seed.set_defaults(func=cmd_seed_demo)

    p_sim = sub.add_parser("similarity", help="Similarity: file vs KB or two KB docs")
    p_sim.add_argument("path", nargs="?", help="File to compare against KB")
    p_sim.add_argument("--doc-a", default="", help="KB document id A")
    p_sim.add_argument("--doc-b", default="", help="KB document id B")
    p_sim.add_argument("--project", default="")
    p_sim.add_argument("--threshold", type=float, default=0.72)
    p_sim.set_defaults(func=cmd_similarity)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
