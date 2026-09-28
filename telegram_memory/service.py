"""Read-only Memory access for Research Memory Bot."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

PLATFORM_ROOT = Path(__file__).resolve().parents[1]
if str(PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATFORM_ROOT))

from research_memory.engine.chat import answer_question
from research_memory.kb.repository import KnowledgeRepository
from research_memory.schema import ChatAnswer

InventoryIntent = Literal["projects", "latest"]


@dataclass(frozen=True)
class ScheduleQuery:
    label: str
    date_from: str
    date_to: str
    items: list[dict[str, Any]]


@dataclass(frozen=True)
class ProjectRow:
    project_id: str
    title: str
    document_count: int
    schedule_count: int
    last_activity: str  # YYYY-MM-DD or ""


@dataclass(frozen=True)
class ProjectInventory:
    projects: list[ProjectRow]
    total_documents: int


@dataclass(frozen=True)
class LatestFolder:
    project_id: str
    title: str
    last_activity: str
    document_count: int
    latest_document_title: str


_PROJECTS_INTENT = re.compile(
    r"(프로젝트\s*(수|몇|개수|목록|리스트)|"
    r"몇\s*개\s*의?\s*프로젝트|"
    r"전체\s*프로젝트|"
    r"과제\s*(수|몇|개수|목록)|"
    r"등록된\s*(프로젝트|과제)|"
    r"\bprojects?\b)",
    re.IGNORECASE,
)
_LATEST_INTENT = re.compile(
    r"(최신\s*(작업\s*)?(폴더|프로젝트|과제)|"
    r"최근\s*(작업\s*)?(폴더|프로젝트|과제)|"
    r"제일\s*최신|"
    r"마지막\s*(작업|폴더|프로젝트)|"
    r"\blatest\b)",
    re.IGNORECASE,
)


def week_bounds(day: date | None = None) -> tuple[date, date]:
    """Monday–Sunday containing ``day`` (default: today)."""
    day = day or date.today()
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return start, end


def detect_inventory_intent(text: str) -> InventoryIntent | None:
    q = (text or "").strip()
    if not q:
        return None
    # Prefer latest when both could match loosely.
    if _LATEST_INTENT.search(q):
        return "latest"
    if _PROJECTS_INTENT.search(q):
        return "projects"
    return None


class MemoryService:
    def __init__(self, repo: KnowledgeRepository | None = None) -> None:
        self.repo = repo or KnowledgeRepository()

    def ask(self, question: str) -> ChatAnswer:
        return answer_question(question.strip(), repo=self.repo)

    def list_today(self, day: date | None = None) -> ScheduleQuery:
        day = day or date.today()
        day_s = day.isoformat()
        items = self.repo.list_schedule_items(date_from=day_s, date_to=day_s)
        return ScheduleQuery(
            label=f"오늘 ({day_s})",
            date_from=day_s,
            date_to=day_s,
            items=items,
        )

    def list_week(self, day: date | None = None) -> ScheduleQuery:
        start, end = week_bounds(day)
        items = self.repo.list_schedule_items(
            date_from=start.isoformat(),
            date_to=end.isoformat(),
        )
        return ScheduleQuery(
            label=f"이번 주 ({start.isoformat()} ~ {end.isoformat()})",
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            items=items,
        )

    def list_project_inventory(self) -> ProjectInventory:
        registered = {
            (p.get("project_id") or "").strip(): p
            for p in self.repo.list_projects()
            if (p.get("project_id") or "").strip()
        }
        docs = self.repo.list_documents()
        by_project: dict[str, list[dict[str, Any]]] = {}
        for d in docs:
            if (d.get("status") or "") != "ready":
                continue
            pid = (d.get("project_id") or "").strip() or "(No project)"
            by_project.setdefault(pid, []).append(d)

        # Include empty registered projects.
        for pid in registered:
            by_project.setdefault(pid, [])

        rows: list[ProjectRow] = []
        for pid, group in by_project.items():
            if pid == "(No project)" and not group:
                continue
            meta = registered.get(pid) or {}
            title = (meta.get("title") or "").strip() or pid
            last = ""
            for d in group:
                day = str(d.get("created_at") or "")[:10]
                if day and day > last:
                    last = day
            if not last:
                last = str(meta.get("created_at") or "")[:10]
            rows.append(
                ProjectRow(
                    project_id=pid,
                    title=title,
                    document_count=len(group),
                    schedule_count=int(meta.get("schedule_count") or 0),
                    last_activity=last,
                )
            )

        # Stable sorts: name → newest activity first → unassigned last
        rows.sort(key=lambda r: r.project_id.lower())
        rows.sort(key=lambda r: r.last_activity or "", reverse=True)
        rows.sort(key=lambda r: r.project_id == "(No project)")
        total_docs = sum(r.document_count for r in rows)
        return ProjectInventory(projects=rows, total_documents=total_docs)

    def latest_folder(self) -> LatestFolder | None:
        inv = self.list_project_inventory()
        candidates = [p for p in inv.projects if p.project_id != "(No project)"]
        if not candidates:
            return None
        candidates.sort(
            key=lambda r: (r.last_activity or "", r.document_count, r.project_id),
            reverse=True,
        )
        top = candidates[0]
        latest_title = ""
        docs = [
            d
            for d in self.repo.list_documents()
            if (d.get("project_id") or "").strip() == top.project_id
            and (d.get("status") or "") == "ready"
        ]
        docs.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
        if docs:
            latest_title = str(docs[0].get("title") or docs[0].get("filename") or "")
        return LatestFolder(
            project_id=top.project_id,
            title=top.title,
            last_activity=top.last_activity,
            document_count=top.document_count,
            latest_document_title=latest_title,
        )


def memory_engine_status() -> str:
    try:
        from research_memory.engine.chat import answer_question as _ask

        _ = _ask
    except Exception as exc:  # noqa: BLE001 — status line only
        return f"error ({type(exc).__name__}: {exc})"
    return "connected (answer_question)"
