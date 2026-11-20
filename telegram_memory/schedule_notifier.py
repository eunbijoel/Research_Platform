"""Daily calendar notices for the primary Research Memory bot.

Runs inside the bot process at 09:00 Asia/Seoul. Does not touch Streamlit.
Sends only to allowlisted users who have opened a private chat, and only
from the primary bot token.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from research_memory.engine.schedule import (
    chip_time_and_title,
    event_type_label,
    item_date_range,
    normalize_event_type,
)

logger = logging.getLogger("research-memory-bot")

KST = ZoneInfo("Asia/Seoul")
NOTIFY_HOUR = 9
KIND_D_MINUS_1 = "d_minus_1"
KIND_D_PLUS_1 = "d_plus_1"

_WEEKDAYS = "월화수목금토일"
_CANCELLED = {"cancelled", "canceled", "cancel", "취소"}

SendFunc = Callable[[int, str], Awaitable[None]]

_D1_HEADING = {
    "meeting": "내일 회의 일정 안내",
    "task": "내일 작업 마감 안내",
    "submission": "내일 제출 일정 안내",
    "milestone": "내일 주요 일정 안내",
}
_D1P_MISSING = {
    "meeting": (
        "어제 회의 내용 업데이트 안내",
        "아직 연결된 회의록이나 업데이트 자료가 없습니다.",
        "회의 내용을 정리하셨다면 Research Platform에서 해당 일정에 첨부해주세요.",
    ),
    "task": (
        "어제 작업 진행상태 안내",
        "아직 연결된 진행상태 자료가 없습니다.",
        "진행상태를 업데이트하셨다면 Research Platform에서 해당 일정에 첨부해주세요.",
    ),
    "submission": (
        "어제 제출 일정 확인",
        "아직 연결된 제출 자료가 없습니다.",
        "제출을 완료하셨다면 Research Platform에서 해당 일정에 첨부해주세요.",
    ),
    "milestone": (
        "어제 마일스톤 결과물 안내",
        "아직 연결된 결과물이 없습니다.",
        "결과물을 정리하셨다면 Research Platform에서 해당 일정에 첨부해주세요.",
    ),
}
_D1P_HAS = {
    "meeting": "회의록 등록 확인",
    "task": "진행상태 업데이트 확인",
    "submission": "제출 완료 여부 확인",
    "milestone": "결과물 등록 확인",
}


@dataclass(frozen=True)
class NoticePlan:
    schedule_id: str
    kind: str
    anchor_date: str
    telegram_user_id: int
    text: str


def kst_today(now: datetime | None = None) -> date:
    current = now or datetime.now(KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=KST)
    return current.astimezone(KST).date()


def seconds_until_next_slot(now: datetime, *, hour: int = NOTIFY_HOUR) -> float:
    """Seconds until the next hour:00 in KST. Zero only when now is exactly that minute."""
    local = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
    target = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if local >= target:
        target = target + timedelta(days=1)
    return max(0.0, (target - local).total_seconds())


def slot_is_due(now: datetime, *, hour: int = NOTIFY_HOUR) -> bool:
    local = now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
    slot = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    return local >= slot


def is_cancelled(item: dict[str, Any]) -> bool:
    raw = str(item.get("status") or "").strip().lower()
    return raw in _CANCELLED


def format_when(day: date, time_str: str | None) -> str:
    text = f"{day.month}월 {day.day}일({_WEEKDAYS[day.weekday()]})"
    if time_str:
        text = f"{text} {time_str}"
    return text


def attachment_names(docs: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for doc in docs:
        name = str(doc.get("filename") or doc.get("title") or "").strip()
        if name:
            names.append(name)
    return names


def _project_label(repo: Any, project_id: str) -> str:
    pid = (project_id or "").strip()
    if not pid:
        return "—"
    try:
        project = repo.get_project(pid)
    except Exception:
        logger.exception("project lookup failed project_id=%s", pid)
        project = None
    if isinstance(project, dict):
        title = str(project.get("title") or "").strip()
        if title:
            return title
    return pid


def format_d_minus_1(
    item: dict[str, Any],
    *,
    project_label: str,
    when_day: date,
) -> str:
    etype = normalize_event_type(item.get("event_type"))
    raw_title = str(item.get("title") or "").strip() or "(제목 없음)"
    time_str, base_title = chip_time_and_title(raw_title, item.get("note"))
    heading = _D1_HEADING.get(etype, _D1_HEADING["task"])
    lines = [
        heading,
        "",
        html.escape(base_title),
        "",
        f"일시: {html.escape(format_when(when_day, time_str))}",
        "",
        f"프로젝트: {html.escape(project_label)}",
        "",
        f"유형: {html.escape(event_type_label(etype))}",
        "",
        "Research Platform에 등록된 일정입니다.",
    ]
    return "\n".join(lines)


def format_d_plus_1(
    item: dict[str, Any],
    *,
    names: list[str],
    platform_url: str,
) -> str:
    etype = normalize_event_type(item.get("event_type"))
    raw_title = str(item.get("title") or "").strip() or "(제목 없음)"
    _time, base_title = chip_time_and_title(raw_title, item.get("note"))
    title = html.escape(base_title)
    if names:
        heading = _D1P_HAS.get(etype, _D1P_HAS["task"])
        if len(names) == 1:
            attached = f"연결된 자료: {html.escape(names[0])}"
        else:
            attached = "연결된 자료:\n" + "\n".join(
                f"· {html.escape(name)}" for name in names
            )
        return "\n".join(
            [
                heading,
                "",
                title,
                "",
                attached,
                "",
                "Research Platform에서 확인할 수 있습니다.",
            ]
        )
    heading, missing, ask = _D1P_MISSING.get(etype, _D1P_MISSING["task"])
    link = platform_link(platform_url)
    return "\n".join([heading, "", title, "", missing, "", ask, "", link])


def platform_link(url: str) -> str:
    href = html.escape((url or "").strip(), quote=True)
    if not href:
        return "Research Platform 열기"
    return f'<a href="{href}">Research Platform 열기</a>'


def resolve_recipients(store: Any, only_user: int | None) -> tuple[list[int], str]:
    """Allowlisted users who already opened a private DM. Groups are never included."""
    allowed = {int(user.telegram_user_id) for user in store.list_allowed_users()}
    started = {int(uid) for uid in store.list_private_dm_user_ids()}
    if only_user is not None:
        uid = int(only_user)
        if uid not in allowed:
            return [], f"skip: user {uid} is not on the allowlist"
        if uid not in started:
            return [], f"skip: user {uid} has not started a private DM"
        return [uid], ""
    return sorted(allowed & started), ""


def _occurrences(repo: Any, today: date) -> list[tuple[dict[str, Any], str, str, date]]:
    tomorrow = today + timedelta(days=1)
    yesterday = today - timedelta(days=1)
    found: list[tuple[dict[str, Any], str, str, date]] = []
    for item in repo.list_schedule_items():
        if not isinstance(item, dict) or is_cancelled(item):
            continue
        span = item_date_range(item)
        if span is None:
            continue
        start, end = span
        schedule_id = str(item.get("id") or "").strip()
        if not schedule_id:
            continue
        if start == tomorrow:
            found.append((item, KIND_D_MINUS_1, start.isoformat(), start))
        if end == yesterday:
            found.append((item, KIND_D_PLUS_1, end.isoformat(), end))
    found.sort(key=lambda row: (row[1], row[2], str(row[0].get("title") or "")))
    return found


def build_plans(
    repo: Any,
    store: Any,
    *,
    today: date,
    platform_url: str,
    only_user: int | None = None,
) -> tuple[list[NoticePlan], str]:
    users, reason = resolve_recipients(store, only_user)
    if not users:
        return [], reason
    plans: list[NoticePlan] = []
    for item, kind, anchor, when_day in _occurrences(repo, today):
        schedule_id = str(item.get("id") or "")
        project = _project_label(repo, str(item.get("project_id") or ""))
        if kind == KIND_D_MINUS_1:
            text = format_d_minus_1(item, project_label=project, when_day=when_day)
        else:
            try:
                docs = repo.list_schedule_attachments(schedule_id)
            except Exception:
                logger.exception("attachment lookup failed schedule_id=%s", schedule_id)
                docs = []
            text = format_d_plus_1(
                item,
                names=attachment_names(list(docs or [])),
                platform_url=platform_url,
            )
        for uid in users:
            if store.notice_status(
                schedule_id=schedule_id,
                kind=kind,
                anchor_date=anchor,
                telegram_user_id=uid,
            ) == "sent":
                continue
            plans.append(
                NoticePlan(
                    schedule_id=schedule_id,
                    kind=kind,
                    anchor_date=anchor,
                    telegram_user_id=uid,
                    text=text,
                )
            )
    return plans, reason


async def dispatch_plans(
    plans: list[NoticePlan],
    store: Any,
    send: SendFunc,
    *,
    owner: str,
    retries: int = 3,
    retry_delay: float = 0.0,
) -> list[NoticePlan]:
    """Send each plan. One failure does not stop the rest. Failed rows stay retryable."""
    pending = list(plans)
    attempts = max(1, retries)
    for attempt in range(attempts):
        failed: list[NoticePlan] = []
        for plan in pending:
            try:
                claimed = store.claim_notice(
                    schedule_id=plan.schedule_id,
                    kind=plan.kind,
                    anchor_date=plan.anchor_date,
                    telegram_user_id=plan.telegram_user_id,
                    owner=owner,
                )
            except Exception as exc:
                logger.warning(
                    "schedule notice claim failed user_id=%s schedule_id=%s kind=%s error=%s",
                    plan.telegram_user_id,
                    plan.schedule_id,
                    plan.kind,
                    type(exc).__name__,
                )
                failed.append(plan)
                continue
            if not claimed:
                continue
            try:
                await send(plan.telegram_user_id, plan.text)
            except Exception as exc:
                logger.warning(
                    "schedule notice failed user_id=%s schedule_id=%s kind=%s error=%s",
                    plan.telegram_user_id,
                    plan.schedule_id,
                    plan.kind,
                    type(exc).__name__,
                )
                store.finish_notice(
                    schedule_id=plan.schedule_id,
                    kind=plan.kind,
                    anchor_date=plan.anchor_date,
                    telegram_user_id=plan.telegram_user_id,
                    status="failed",
                    error=type(exc).__name__,
                )
                failed.append(plan)
                continue
            store.finish_notice(
                schedule_id=plan.schedule_id,
                kind=plan.kind,
                anchor_date=plan.anchor_date,
                telegram_user_id=plan.telegram_user_id,
                status="sent",
                error="",
            )
        pending = failed
        if not pending or attempt >= attempts - 1:
            break
        if retry_delay > 0:
            await asyncio.sleep(retry_delay)
    return pending


class ScheduleNotifier:
    """One daily loop. A SQLite lock keeps a second bot process from looping too."""

    def __init__(
        self,
        *,
        repo: Any,
        store: Any,
        platform_url: str,
        retry_delay: float = 60.0,
    ) -> None:
        self.repo = repo
        self.store = store
        self.platform_url = platform_url
        self.retry_delay = retry_delay
        self.owner = str(os.getpid())

    async def run_once(
        self,
        bot: Any,
        *,
        today: date | None = None,
        only_user: int | None = None,
        dry_run: bool = False,
    ) -> list[NoticePlan]:
        today = today or kst_today()
        plans, reason = build_plans(
            self.repo,
            self.store,
            today=today,
            platform_url=self.platform_url,
            only_user=only_user,
        )
        if reason:
            logger.info("schedule notifier %s", reason)
        logger.info(
            "schedule notifier planned=%s today=%s dry_run=%s",
            len(plans),
            today.isoformat(),
            dry_run,
        )
        if dry_run or not plans:
            return plans

        async def send(user_id: int, text: str) -> None:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await dispatch_plans(
            plans,
            self.store,
            send,
            owner=self.owner,
            retry_delay=self.retry_delay,
        )
        return plans

    async def serve(self, bot: Any, stop_event: asyncio.Event) -> None:
        if not self.store.acquire_scheduler(self.owner):
            logger.info("schedule notifier skipped; another process holds the lock")
            return
        logger.info("schedule notifier started hour=%s tz=Asia/Seoul", NOTIFY_HOUR)
        ran_for: date | None = None
        try:
            while not stop_event.is_set():
                if not self.store.touch_scheduler(self.owner):
                    logger.warning("schedule notifier lost the lock; stopping")
                    return
                now = datetime.now(KST)
                if slot_is_due(now) and ran_for != now.date():
                    try:
                        await self.run_once(bot)
                    except Exception:
                        logger.exception("schedule notifier run failed")
                    else:
                        ran_for = now.date()
                timeout = min(seconds_until_next_slot(datetime.now(KST)), 60.0)
                if timeout < 1:
                    timeout = 1.0
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=timeout)
                except TimeoutError:
                    continue
        finally:
            self.store.release_scheduler(self.owner)
