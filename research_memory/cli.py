from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_memory.config import ensure_data_dirs
from research_memory.engine.chat import answer_question
from research_memory.engine.proposal import run_proposal_pipeline
from research_memory.engine.similarity import compare_kb_documents, compare_upload_vs_kb
from research_memory.engine.schedule import (
    event_type_label,
    list_month_items,
    normalize_event_type,
    normalize_status,
    status_label,
)
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


def cmd_schedule(args: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    if args.add:
        if not args.project or not args.title or not args.date:
            raise SystemExit("--project, --title, --date required for --add")
        sid = repo.add_schedule_item(
            project_id=args.project,
            title=args.title,
            event_type=normalize_event_type(args.type),
            date=args.date,
            status=normalize_status(args.status),
            note=args.note or "",
        )
        print(json.dumps({"ok": True, "id": sid}, ensure_ascii=False))
        return

    year = args.year
    month = args.month
    if year and month:
        items = list_month_items(
            year,
            month,
            repo=repo,
            project_id=args.project or None,
            status=normalize_status(args.status) if args.status else None,
        )
    else:
        items = repo.list_schedule_items(
            project_id=args.project or None,
            status=normalize_status(args.status) if args.status else None,
        )
    print(json.dumps({"count": len(items)}, ensure_ascii=False))
    for it in items:
        print(
            f"- {it.get('date')} [{event_type_label(it.get('event_type'))}/"
            f"{status_label(it.get('status'))}] {it.get('title')} "
            f"project={it.get('project_id')}"
        )


def cmd_rebuild_index(_: argparse.Namespace) -> None:
    repo = KnowledgeRepository()
    n = repo.rebuild_index()
    print(json.dumps({"chunks": n, **repo.retrieval_status()}, ensure_ascii=False))


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

    p_sched = sub.add_parser("schedule", help="List or add project schedule items")
    p_sched.add_argument("--project", default="")
    p_sched.add_argument("--year", type=int, default=0)
    p_sched.add_argument("--month", type=int, default=0)
    p_sched.add_argument("--status", default="")
    p_sched.add_argument("--add", action="store_true", help="Add a schedule item")
    p_sched.add_argument("--title", default="")
    p_sched.add_argument("--date", default="", help="YYYY-MM-DD")
    p_sched.add_argument(
        "--type",
        default="task",
        help="meeting|submission|task|milestone",
    )
    p_sched.add_argument("--note", default="")
    p_sched.set_defaults(func=cmd_schedule)

    p_re = sub.add_parser("rebuild-index", help="Rebuild vector + TF-IDF retrieval indexes")
    p_re.set_defaults(func=cmd_rebuild_index)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
