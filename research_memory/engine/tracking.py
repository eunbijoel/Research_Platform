from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from research_memory.kb.repository import KnowledgeRepository

_TOKEN_RE = re.compile(r"[A-Za-z가-힣0-9]{2,}")


def project_timeline(
    project_id: str,
    *,
    repo: KnowledgeRepository | None = None,
) -> dict[str, Any]:
    """Build a timeline of milestones + ingested documents for a project."""
    repo = repo or KnowledgeRepository()
    project = repo.get_project(project_id)
    if not project:
        return {"ok": False, "error": f"Unknown project: {project_id}"}

    milestones = repo.list_milestones(project_id)
    documents = repo.list_documents_for_project(project_id)
    events: list[dict[str, Any]] = []

    for m in milestones:
        events.append(
            {
                "kind": "milestone",
                "date": m.get("due_date") or m.get("created_at", "")[:10],
                "title": m.get("title"),
                "status": m.get("status"),
                "deliverable_type": m.get("deliverable_type"),
                "id": m.get("id"),
            }
        )
    for d in documents:
        events.append(
            {
                "kind": "document",
                "date": (d.get("created_at") or "")[:10],
                "title": d.get("filename"),
                "status": d.get("status"),
                "deliverable_type": d.get("doc_type"),
                "id": d.get("id"),
            }
        )
    events.sort(key=lambda e: (e.get("date") or "9999", e["kind"], e.get("title") or ""))
    return {
        "ok": True,
        "project": project,
        "milestones": milestones,
        "documents": documents,
        "events": events,
    }


def gap_report(
    project_id: str,
    *,
    repo: KnowledgeRepository | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """
    Compare planned milestones vs Memory documents.

    Matching heuristic:
    1) explicit linked_document_id
    2) deliverable_type == doc_type
    3) keyword overlap between expected_keywords/title and document title/filename/text head
    """
    repo = repo or KnowledgeRepository()
    project = repo.get_project(project_id)
    if not project:
        return {"ok": False, "error": f"Unknown project: {project_id}"}

    today = today or date.today()
    milestones = repo.list_milestones(project_id)
    documents = repo.list_documents_for_project(project_id)
    used_docs: set[str] = set()
    rows: list[dict[str, Any]] = []

    for m in milestones:
        match = _match_document(m, documents, used_docs)
        due = _parse_date(m.get("due_date") or "")
        status = m.get("status") or "planned"
        if match:
            used_docs.add(match["id"])
            coverage = "covered"
            if status == "planned":
                status = "done"
        else:
            coverage = "missing"
            if due and due < today and status not in {"done", "cancelled"}:
                status = "overdue"

        rows.append(
            {
                "milestone_id": m["id"],
                "title": m.get("title"),
                "due_date": m.get("due_date") or "",
                "deliverable_type": m.get("deliverable_type") or "other",
                "planned_status": m.get("status"),
                "coverage": coverage,
                "effective_status": status,
                "matched_document_id": match["id"] if match else "",
                "matched_filename": match["filename"] if match else "",
                "match_reason": match.get("_reason", "") if match else "",
            }
        )

    orphan_docs = [
        {
            "id": d["id"],
            "filename": d["filename"],
            "doc_type": d.get("doc_type"),
            "created_at": d.get("created_at"),
        }
        for d in documents
        if d["id"] not in used_docs
    ]

    covered = sum(1 for r in rows if r["coverage"] == "covered")
    missing = sum(1 for r in rows if r["coverage"] == "missing")
    overdue = sum(1 for r in rows if r["effective_status"] == "overdue")

    return {
        "ok": True,
        "project": project,
        "summary": {
            "milestones": len(rows),
            "covered": covered,
            "missing": missing,
            "overdue": overdue,
            "orphan_documents": len(orphan_docs),
            "coverage_ratio": (covered / len(rows)) if rows else 1.0,
        },
        "gaps": rows,
        "orphan_documents": orphan_docs,
    }


def auto_link_milestones(
    project_id: str,
    *,
    repo: KnowledgeRepository | None = None,
) -> dict[str, Any]:
    """Persist linked_document_id / status for covered milestones."""
    repo = repo or KnowledgeRepository()
    report = gap_report(project_id, repo=repo)
    if not report.get("ok"):
        return report
    updated = 0
    for row in report["gaps"]:
        if row["coverage"] != "covered" or not row["matched_document_id"]:
            continue
        repo.update_milestone(
            row["milestone_id"],
            linked_document_id=row["matched_document_id"],
            status="done",
        )
        updated += 1
    return {"ok": True, "updated": updated, "report": gap_report(project_id, repo=repo)}


def _match_document(
    milestone: dict[str, Any],
    documents: list[dict[str, Any]],
    used: set[str],
) -> dict[str, Any] | None:
    linked = milestone.get("linked_document_id") or ""
    if linked:
        for d in documents:
            if d["id"] == linked:
                out = dict(d)
                out["_reason"] = "explicit_link"
                return out

    dtype = (milestone.get("deliverable_type") or "").lower()
    keywords = _tokens(
        f"{milestone.get('title', '')} {milestone.get('expected_keywords', '')}"
    )
    best: tuple[float, dict[str, Any], str] | None = None
    for d in documents:
        if d["id"] in used:
            continue
        score = 0.0
        reason = ""
        if dtype and dtype != "other" and (d.get("doc_type") or "").lower() == dtype:
            score += 0.45
            reason = "doc_type"
        blob = f"{d.get('filename', '')} {d.get('title', '')} {(d.get('full_text') or '')[:1500]}"
        overlap = keywords & _tokens(blob)
        if overlap:
            score += min(0.55, 0.15 * len(overlap))
            reason = (reason + "+keywords").strip("+") if reason else "keywords"
        if score >= 0.45:
            if best is None or score > best[0]:
                cand = dict(d)
                cand["_reason"] = reason
                best = (score, cand, reason)
    return best[1] if best else None


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "")}


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None
