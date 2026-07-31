from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_memory.config import PROJECT_ROOT, ensure_data_dirs
from research_memory.engine.chat import answer_question
from research_memory.engine.proposal import run_proposal_pipeline
from research_memory.engine.similarity import compare_kb_documents, compare_upload_vs_kb
from research_memory.engine.tracking import (
    auto_link_milestones,
    gap_report,
    seed_demo_project,
)
from research_memory.eval_retrieval import evaluate_retrieval
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

    project_by_file = {
        "center_overview.md": "DEMO-2026",
        "proposal_excerpt.md": "DEMO-2026",
        "meeting_note.md": "DEMO-2026",
        "ald_process_note.md": "ALD-2024",
        "ald_annual_report.md": "ALD-2024",
        "meeting_ald_hwp.md": "ALD-2024",
        "hwp_analyst_design.md": "HWP-ANALYST-2025",
        "hwp_analyst_trial.md": "HWP-ANALYST-2025",
        "kmx_concept_note.md": "KMX-2025",
        "max_integration_survey.md": "MAX-2026",
        "rm_proposal_2026.md": "RM-PROPOSAL-2026",
        "proposal_intel_note.md": "RM-PROPOSAL-2026",
    }

    # Fallback inline bodies for the original 3 if files missing
    fallback = {
        "center_overview.md": """# KETI 소프트웨어센터 연구 개요 (데모)\n\n과제명: 산업 데이터 스페이스 연계형 Research Memory\n""",
        "proposal_excerpt.md": """# 제안서 발췌 (데모)\n\n과제명: Manufacturing-X 연계 문서 지능\n""",
        "meeting_note.md": """# 회의록 (데모)\n\nPhase 1은 Chat(인용 강제)까지\n""",
    }

    repo = KnowledgeRepository()
    for name, project_id in project_by_file.items():
        path = demo_dir / name
        if not path.exists():
            if name in fallback:
                path.write_text(fallback[name], encoding="utf-8")
            else:
                print(json.dumps({"ok": False, "file": name, "error": "missing"}, ensure_ascii=False))
                continue
        result = ingest_file(path, repo=repo, project_id=project_id)
        result["project_id"] = project_id
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


def cmd_proposal(args: argparse.Namespace) -> None:
    path = Path(args.path)
    result = run_proposal_pipeline(
        path.read_bytes(),
        path.name,
        project_id=args.project or None,
        selected_role_index=args.role_index,
    )
    if not result.get("ok"):
        print(json.dumps({"ok": False, "error": result.get("error")}, ensure_ascii=False))
        return
    summary = {
        "ok": True,
        "project_name": (result.get("rfp") or {}).get("project_name"),
        "roles": len(result.get("roles") or []),
        "evidence": len(result.get("evidence") or []),
        "selected_role": (result.get("selected_role") or {}).get("role"),
        "draft_mode": (result.get("draft") or {}).get("mode"),
    }
    print(json.dumps(summary, ensure_ascii=False))
    out = Path(args.out) if args.out else Path("proposal_draft.md")
    out.write_text(result.get("markdown") or "", encoding="utf-8")
    print(f"wrote {out}")


def cmd_milestone(args: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    if args.seed:
        print(json.dumps(seed_demo_project(repo=repo, project_id=args.project), ensure_ascii=False))
        return
    if args.autolink:
        print(json.dumps(auto_link_milestones(args.project, repo=repo), ensure_ascii=False))
        return
    report = gap_report(args.project, repo=repo)
    print(json.dumps(report.get("summary"), ensure_ascii=False))
    for g in report.get("gaps") or []:
        print(
            f"- [{g['coverage']}/{g['effective_status']}] {g['title']} "
            f"due={g['due_date'] or '-'} → {g['matched_filename'] or 'MISSING'}"
        )


def cmd_rebuild_index(_: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    n = repo.rebuild_index()
    print(json.dumps({"chunks": n, **repo.retrieval_status()}, ensure_ascii=False))


def cmd_eval(args: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    if args.rebuild:
        repo.rebuild_index()
    result = evaluate_retrieval(repo=repo, top_k=args.top_k)
    summary = {
        "n": result["n"],
        "top_k": result["top_k"],
        "recall_at_k": round(result["recall_at_k"], 3),
        "mrr": round(result["mrr"], 3),
        "hits": result["hits"],
        "index": result["index"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    for row in result["rows"]:
        mark = "OK" if row["hit"] else "MISS"
        print(
            f"[{mark}] {row['id']} backend={row['backend']} rank={row['rank']} "
            f"got={row['retrieved_files'][:3]}"
        )


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

    p_prop = sub.add_parser("proposal", help="RFP + KB evidence → center draft")
    p_prop.add_argument("path", help="RFP file path")
    p_prop.add_argument("--project", default="")
    p_prop.add_argument("--role-index", type=int, default=0)
    p_prop.add_argument("--out", default="proposal_draft.md")
    p_prop.set_defaults(func=cmd_proposal)

    p_mile = sub.add_parser("milestone", help="Project milestone gap report")
    p_mile.add_argument("--project", default="DEMO-2026")
    p_mile.add_argument("--seed", action="store_true", help="Seed demo project/milestones")
    p_mile.add_argument("--autolink", action="store_true", help="Persist matched links")
    p_mile.set_defaults(func=cmd_milestone)

    p_re = sub.add_parser("rebuild-index", help="Rebuild vector + TF-IDF retrieval indexes")
    p_re.set_defaults(func=cmd_rebuild_index)

    p_eval = sub.add_parser("eval", help="Run retrieval evaluation on gold QA set")
    p_eval.add_argument("--top-k", type=int, default=5)
    p_eval.add_argument("--rebuild", action="store_true")
    p_eval.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
