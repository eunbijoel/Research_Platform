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
ScheduleIntent = Literal["today", "week", "last_meeting", "meeting_notes"]


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


@dataclass(frozen=True)
class MeetingContext:
    item: dict[str, Any]
    attachments: list[dict[str, Any]]


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
_TODAY_SCHEDULE = re.compile(
    r"(오늘\s*(일정|스케줄|미팅|회의)|"
    r"오늘\s*뭐\s*(있어|있지)|"
    r"\btoday\b)",
    re.IGNORECASE,
)
_WEEK_SCHEDULE = re.compile(
    r"(이번\s*주\s*(일정|스케줄|미팅|회의)|"
    r"주간\s*(일정|스케줄)|"
    r"\bthis\s*week\b)",
    re.IGNORECASE,
)
_MEETING_NOTES = re.compile(
    r"(회의록|미팅\s*록|"
    r"(그날|그\s*날|당시|그때)\s*(어떤\s*)?(대화|얘기|논|안건|내용)|"
    r"(뭐|무엇)\s*(를?\s*)?(얘기|대화|논의|결정)|"
    r"(논의|안건|결정)\s*(사항|내용)|"
    r"(첨부|연결된)\s*(회의록|문서)|"
    r"meeting\s*(notes?|minutes))",
    re.IGNORECASE,
)
_LAST_MEETING = re.compile(
    r"(마지막|최근|지난|제일\s*최근)\s*(미팅|회의)|"
    r"(미팅|회의)\s*(이\s*)?(뭐였|있었|언제)|"
    r"last\s*meeting",
    re.IGNORECASE,
)
_DATE_ISO = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")
_DATE_KR = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")


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


def detect_schedule_intent(text: str) -> ScheduleIntent | None:
    q = (text or "").strip()
    if not q:
        return None
    # Content about a meeting/notes beats listing.
    if _MEETING_NOTES.search(q):
        return "meeting_notes"
    if _LAST_MEETING.search(q):
        return "last_meeting"
    if _TODAY_SCHEDULE.search(q):
        return "today"
    if _WEEK_SCHEDULE.search(q):
        return "week"
    return None


def parse_mentioned_date(text: str, *, today: date | None = None) -> date | None:
    """Pull an explicit calendar day from the question, if any."""
    today = today or date.today()
    q = text or ""
    m = _DATE_ISO.search(q)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = _DATE_KR.search(q)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year - 1):
            try:
                return date(year, month, day)
            except ValueError:
                continue
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

    def list_meetings(self) -> list[dict[str, Any]]:
        items = self.repo.list_schedule_items()
        meetings = [
            i
            for i in items
            if str(i.get("event_type") or "").strip().lower() == "meeting"
        ]
        meetings.sort(
            key=lambda i: (
                str(i.get("date") or ""),
                str(i.get("end_date") or i.get("date") or ""),
                str(i.get("created_at") or ""),
            ),
            reverse=True,
        )
        return meetings

    def latest_meeting(self) -> MeetingContext | None:
        meetings = self.list_meetings()
        if not meetings:
            return None
        item = meetings[0]
        atts = self.repo.list_schedule_attachments(str(item.get("id") or ""))
        return MeetingContext(item=item, attachments=atts)

    def meeting_on(self, day: date) -> MeetingContext | None:
        day_s = day.isoformat()
        items = self.repo.list_schedule_items(date_from=day_s, date_to=day_s)
        meetings = [
            i
            for i in items
            if str(i.get("event_type") or "").strip().lower() == "meeting"
        ]
        if not meetings:
            return None
        meetings.sort(
            key=lambda i: (str(i.get("created_at") or ""), str(i.get("title") or "")),
            reverse=True,
        )
        item = meetings[0]
        atts = self.repo.list_schedule_attachments(str(item.get("id") or ""))
        return MeetingContext(item=item, attachments=atts)

    def resolve_meeting(self, question: str) -> MeetingContext | None:
        mentioned = parse_mentioned_date(question)
        if mentioned is not None:
            hit = self.meeting_on(mentioned)
            if hit is not None:
                return hit
        return self.latest_meeting()

    def ask_meeting_notes(self, question: str, ctx: MeetingContext | None = None) -> ChatAnswer:
        ctx = ctx or self.resolve_meeting(question)
        if ctx is None:
            return ChatAnswer(
                answer="일정에서 회의를 찾지 못했습니다. Home 일정에 회의를 등록해 주세요.",
                citations=[],
                refused=True,
                mode="refused",
            )
        ready = [
            a
            for a in ctx.attachments
            if (a.get("status") or "") == "ready" and (a.get("id") or "").strip()
        ]
        if not ready:
            title = str(ctx.item.get("title") or "").strip() or "(제목 없음)"
            day = str(ctx.item.get("date") or "")[:10] or "—"
            return ChatAnswer(
                answer=(
                    f"{day} «{title}» 일정은 있지만 첨부된 회의록이 없습니다. "
                    "Home 일정에서 회의록을 첨부하면 내용 질문에 답할 수 있습니다."
                ),
                citations=[],
                refused=True,
                mode="refused",
            )
        doc_ids = [str(a["id"]) for a in ready]
        return answer_question(
            question.strip(),
            repo=self.repo,
            document_ids=doc_ids,
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
