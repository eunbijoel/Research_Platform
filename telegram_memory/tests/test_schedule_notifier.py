from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from research_memory.kb.repository import KnowledgeRepository
from schedule_notifier import (
    KIND_D_MINUS_1,
    KIND_D_PLUS_1,
    build_plans,
    dispatch_plans,
    format_when,
    kst_today,
    seconds_until_next_slot,
    slot_is_due,
)
from storage import ChatLogStore

KST = ZoneInfo("Asia/Seoul")
TODAY = date(2026, 11, 24)  # Tuesday; tomorrow is Wednesday 11/25


def _repo(tmp_path: Path) -> KnowledgeRepository:
    return KnowledgeRepository(
        db_path=tmp_path / "memory.sqlite3",
        index_path=tmp_path / "index",
    )


def _store(tmp_path: Path) -> ChatLogStore:
    store = ChatLogStore(tmp_path / "chat.sqlite3")
    store.add_allowed_user(100, "Admin")
    store.add_allowed_user(200, "Other")
    store.note_private_dm(100)
    store.note_private_dm(200)
    return store


def _add_item(
    repo: KnowledgeRepository,
    *,
    title: str,
    event_type: str,
    day: str,
    end: str | None = None,
    status: str = "planned",
    project_id: str = "mx",
) -> str:
    repo.upsert_project(project_id=project_id, title="Manufacturing-X")
    return repo.add_schedule_item(
        project_id=project_id,
        title=title,
        event_type=event_type,
        date=day,
        end_date=end or day,
        status=status,
    )


def test_format_when_korean_weekday() -> None:
    assert format_when(date(2026, 11, 25), "14:00") == "11월 25일(수) 14:00"
    assert format_when(date(2026, 11, 25), None) == "11월 25일(수)"


def test_slot_uses_korea_time() -> None:
    before = datetime(2026, 11, 24, 8, 59, tzinfo=KST)
    after = datetime(2026, 11, 24, 9, 0, tzinfo=KST)
    assert slot_is_due(before) is False
    assert slot_is_due(after) is True
    assert seconds_until_next_slot(before) == 60
    assert kst_today(datetime(2026, 11, 24, 20, 0, tzinfo=ZoneInfo("UTC"))) == date(
        2026, 11, 25
    )


def test_d_minus_1_and_d_plus_1_by_type(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = _store(tmp_path)
    _add_item(
        repo,
        title="14:00 KMX 플랫폼 개발 회의",
        event_type="meeting",
        day="2026-11-25",
    )
    ended = _add_item(
        repo,
        title="KMX 플랫폼 개발 회의",
        event_type="meeting",
        day="2026-11-23",
    )
    _add_item(repo, title="주간 작업", event_type="task", day="2026-11-23")
    _add_item(repo, title="보고서 제출", event_type="submission", day="2026-11-25")
    _add_item(repo, title="1차 마일스톤", event_type="milestone", day="2026-11-23")
    doc_id = repo.insert_document(
        filename="KMX_회의록_1125.hwpx",
        file_type="hwpx",
        content_hash="hash-notes",
        stored_path="notes.hwpx",
        full_text="회의록",
        metadata={"title": "회의록", "project_id": "mx"},
        facts=[],
        chunks=[],
    )
    repo.attach_schedule_document(ended, doc_id)

    plans, reason = build_plans(
        repo,
        store,
        today=TODAY,
        platform_url="http://bigsoft.iptime.org:51100/",
    )
    assert reason == ""
    by_title = {(p.kind, p.text.split("\n")[2]): p.text for p in plans if p.telegram_user_id == 100}

    meeting_d1 = by_title[(KIND_D_MINUS_1, "KMX 플랫폼 개발 회의")]
    assert "내일 회의 일정 안내" in meeting_d1
    assert "일시: 11월 25일(수) 14:00" in meeting_d1
    assert "프로젝트: Manufacturing-X" in meeting_d1
    assert "유형: 회의" in meeting_d1

    submission_d1 = by_title[(KIND_D_MINUS_1, "보고서 제출")]
    assert "내일 제출 일정 안내" in submission_d1
    assert "유형: 제출" in submission_d1

    meeting_d1p = by_title[(KIND_D_PLUS_1, "KMX 플랫폼 개발 회의")]
    assert "회의록 등록 확인" in meeting_d1p
    assert "연결된 자료: KMX_회의록_1125.hwpx" in meeting_d1p

    task_d1p = by_title[(KIND_D_PLUS_1, "주간 작업")]
    assert "어제 작업 진행상태 안내" in task_d1p
    assert "아직 연결된 진행상태 자료가 없습니다." in task_d1p
    assert "Research Platform 열기" in task_d1p
    assert "http://bigsoft.iptime.org:51100/" in task_d1p

    milestone = by_title[(KIND_D_PLUS_1, "1차 마일스톤")]
    assert "어제 마일스톤 결과물 안내" in milestone
    assert "아직 연결된 결과물이 없습니다." in milestone

    assert all(p.telegram_user_id in {100, 200} for p in plans)
    assert store.notice_status(
        schedule_id=ended,
        kind=KIND_D_PLUS_1,
        anchor_date="2026-11-23",
        telegram_user_id=100,
    ) is None


def test_skips_cancelled_and_unrelated_days(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = _store(tmp_path)
    _add_item(
        repo,
        title="취소된 회의",
        event_type="meeting",
        day="2026-11-25",
        status="cancelled",
    )
    _add_item(repo, title="오늘 회의", event_type="meeting", day="2026-11-24")
    _add_item(
        repo,
        title="진행중인 작업",
        event_type="task",
        day="2026-11-20",
        end="2026-11-26",
    )
    plans, _reason = build_plans(
        repo, store, today=TODAY, platform_url="http://example/"
    )
    titles = "\n".join(p.text for p in plans)
    assert "취소된 회의" not in titles
    assert "오늘 회의" not in titles
    assert "진행중인 작업" not in titles


def test_only_allowlisted_private_dm_and_only_user(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = ChatLogStore(tmp_path / "chat.sqlite3")
    store.add_allowed_user(100, "Admin")
    store.add_allowed_user(300, "No DM")
    store.add_turn(
        telegram_user_id=100,
        telegram_chat_id=100,
        question="/start",
        answer="hi",
    )
    store.add_turn(
        telegram_user_id=999,
        telegram_chat_id=999,
        question="group?",
        answer="no",
    )
    _add_item(repo, title="14:00 회의", event_type="meeting", day="2026-11-25")

    plans, _reason = build_plans(
        repo, store, today=TODAY, platform_url="http://example/"
    )
    assert {p.telegram_user_id for p in plans} == {100}

    none, reason = build_plans(
        repo, store, today=TODAY, platform_url="http://example/", only_user=300
    )
    assert none == []
    assert "private DM" in reason

    blocked, reason = build_plans(
        repo, store, today=TODAY, platform_url="http://example/", only_user=999
    )
    assert blocked == []
    assert "allowlist" in reason


def test_dispatch_retries_one_failure_without_stopping(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    store = _store(tmp_path)
    _add_item(repo, title="14:00 회의", event_type="meeting", day="2026-11-25")
    plans, _reason = build_plans(
        repo, store, today=TODAY, platform_url="http://example/", only_user=100
    )
    assert len(plans) == 1
    calls = {"n": 0}

    async def send(user_id: int, text: str) -> None:
        del user_id, text
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bot blocked")

    failed = asyncio.run(
        dispatch_plans(plans, store, send, owner="test", retries=2, retry_delay=0)
    )
    assert failed == []
    assert calls["n"] == 2
    assert (
        store.notice_status(
            schedule_id=plans[0].schedule_id,
            kind=KIND_D_MINUS_1,
            anchor_date="2026-11-25",
            telegram_user_id=100,
        )
        == "sent"
    )

    async def send_again(user_id: int, text: str) -> None:
        del user_id, text
        raise AssertionError("should not send twice")

    again = asyncio.run(
        dispatch_plans(plans, store, send_again, owner="test", retries=1)
    )
    assert again == []


def test_scheduler_lock_is_single_owner(tmp_path: Path) -> None:
    store = ChatLogStore(tmp_path / "chat.sqlite3")
    assert store.acquire_scheduler("proc-a", stale_seconds=600) is True
    assert store.acquire_scheduler("proc-b", stale_seconds=600) is False
    assert store.touch_scheduler("proc-a") is True
    assert store.touch_scheduler("proc-b") is False
    store.release_scheduler("proc-a")
    assert store.acquire_scheduler("proc-b", stale_seconds=600) is True
