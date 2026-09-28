"""Telegram handlers for Research Memory Bot."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from auth import TelegramAuth, auth_ok
from format_reply import (
    format_latest,
    format_meeting,
    format_projects,
    format_reply,
    format_schedule,
)
from service import detect_inventory_intent, detect_schedule_intent

if TYPE_CHECKING:
    from app import AppContext

logger = logging.getLogger("research-memory-bot")

HELP_TEXT = """Research Memory Bot

이동 중에 연구자료·연구기록을 자연어로 묻고, 근거와 출처를 확인하는 봇입니다.

- /start, /help : 이 안내
- /projects : 프로젝트 폴더 목록·개수 (읽기)
- /latest : 최근 활동 폴더 (읽기)
- /today : 오늘 일정 (읽기)
- /week : 이번 주 일정 (월~일, 읽기)
- 일반 채팅 : Memory 문서에서 검색해 답합니다. 근거가 없으면 거절합니다.
  (프로젝트 수·최신 폴더·오늘/주간 일정·마지막 회의·첨부 회의록 질문은 자동 처리)
- 저장·수정은 하지 않습니다 (읽기 전용).
"""


class TelegramBotApp:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.auth = TelegramAuth(
            allowed_user_id=ctx.settings.telegram_allowed_user_id,
            allowed_chat_id=ctx.settings.telegram_allowed_chat_id,
        )

    def build(self) -> Application:
        app = Application.builder().token(self.ctx.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler("start", self.on_start))
        app.add_handler(CommandHandler("help", self.on_help))
        app.add_handler(CommandHandler("projects", self.on_projects))
        app.add_handler(CommandHandler("latest", self.on_latest))
        app.add_handler(CommandHandler("today", self.on_today))
        app.add_handler(CommandHandler("week", self.on_week))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        return app

    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        if update.effective_message:
            await update.effective_message.reply_text(HELP_TEXT)

    async def on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        if update.effective_message:
            await update.effective_message.reply_text(HELP_TEXT)

    async def on_projects(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_inventory(update, "projects", "프로젝트 목록")

    async def on_latest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_inventory(update, "latest", "최근 활동 폴더")

    async def on_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_schedule(update, self.ctx.memory.list_today, "오늘 일정")

    async def on_week(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply_schedule(update, self.ctx.memory.list_week, "이번 주 일정")

    async def _reply_inventory(
        self,
        update: Update,
        kind: str,
        waiting_label: str,
    ) -> None:
        if not await self._gate(update):
            return
        message = update.effective_message
        if message is None:
            return
        status = await message.reply_text(f"{waiting_label}을 불러오는 중…")
        try:
            if kind == "latest":
                payload = await asyncio.to_thread(self.ctx.memory.latest_folder)
                chunks = format_latest(payload)
            else:
                payload = await asyncio.to_thread(self.ctx.memory.list_project_inventory)
                chunks = format_projects(payload)
        except Exception:
            logger.exception("inventory list failed")
            await status.edit_text("폴더 정보를 불러오지 못했습니다.")
            return
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)

    async def _reply_schedule(
        self,
        update: Update,
        loader: Callable[[], Any],
        waiting_label: str,
    ) -> None:
        if not await self._gate(update):
            return
        message = update.effective_message
        if message is None:
            return
        status = await message.reply_text(f"{waiting_label}을 불러오는 중…")
        try:
            query = await asyncio.to_thread(loader)
        except Exception:
            logger.exception("schedule list failed")
            await status.edit_text("일정을 불러오지 못했습니다.")
            return
        chunks = format_schedule(query)
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)

    async def _reply_last_meeting(self, update: Update) -> None:
        if not await self._gate(update):
            return
        message = update.effective_message
        if message is None:
            return
        status = await message.reply_text("최근 회의를 찾는 중…")
        try:
            ctx = await asyncio.to_thread(self.ctx.memory.latest_meeting)
        except Exception:
            logger.exception("latest meeting failed")
            await status.edit_text("회의 일정을 불러오지 못했습니다.")
            return
        chunks = format_meeting(ctx)
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)

    async def _reply_meeting_notes(self, update: Update, question: str) -> None:
        if not await self._gate(update):
            return
        message = update.effective_message
        if message is None:
            return
        status = await message.reply_text("첨부 회의록에서 근거를 찾는 중…")
        user_id = int(update.effective_user.id) if update.effective_user else 0
        chat_id = int(update.effective_chat.id) if update.effective_chat else 0
        try:
            meeting = await asyncio.to_thread(self.ctx.memory.resolve_meeting, question)
            result = await asyncio.to_thread(
                self.ctx.memory.ask_meeting_notes, question, meeting
            )
        except Exception:
            logger.exception("meeting notes ask failed")
            await status.edit_text("회의록을 불러오지 못했습니다.")
            self._log_turn(
                user_id=user_id,
                chat_id=chat_id,
                question=question,
                answer="회의록을 불러오지 못했습니다.",
                refused=True,
                mode="error",
                citations=[],
            )
            return

        header_chunks = format_meeting(meeting, heading="🗓️ 대상 회의")
        answer_chunks = format_reply(result, repo=self.ctx.memory.repo)
        # Lead with which meeting we used, then the RAG answer.
        first = header_chunks[0]
        if answer_chunks:
            combined = f"{first}\n\n{answer_chunks[0]}"
            out = [combined] + header_chunks[1:] + answer_chunks[1:]
        else:
            out = header_chunks
        # Respect Telegram length by re-splitting if needed.
        from format_reply import _split_telegram

        chunks = _split_telegram(out[0]) + out[1:]
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)
        self._log_turn(
            user_id=user_id,
            chat_id=chat_id,
            question=question,
            answer=result.answer,
            refused=bool(result.refused) or result.answer.startswith(
                "메모리에 근거가 없어 답할 수 없습니다"
            ),
            mode=result.mode,
            citations=[]
            if result.answer.startswith("메모리에 근거가 없어 답할 수 없습니다")
            else result.citations,
        )

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._gate(update):
            return
        message = update.effective_message
        if message is None:
            return
        question = (message.text or "").strip()
        if not question:
            return

        inv = detect_inventory_intent(question)
        if inv == "projects":
            await self._reply_inventory(update, "projects", "프로젝트 목록")
            return
        if inv == "latest":
            await self._reply_inventory(update, "latest", "최근 활동 폴더")
            return

        sched = detect_schedule_intent(question)
        if sched == "today":
            await self._reply_schedule(update, self.ctx.memory.list_today, "오늘 일정")
            return
        if sched == "week":
            await self._reply_schedule(update, self.ctx.memory.list_week, "이번 주 일정")
            return
        if sched == "last_meeting":
            await self._reply_last_meeting(update)
            return
        if sched == "meeting_notes":
            await self._reply_meeting_notes(update, question)
            return

        status = await message.reply_text("Memory에서 근거를 찾는 중…")
        user_id = int(update.effective_user.id) if update.effective_user else 0
        chat_id = int(update.effective_chat.id) if update.effective_chat else 0
        try:
            result = await asyncio.to_thread(self.ctx.memory.ask, question)
        except Exception:
            logger.exception("memory ask failed")
            await status.edit_text("Memory에 연결하지 못했습니다.")
            self._log_turn(
                user_id=user_id,
                chat_id=chat_id,
                question=question,
                answer="Memory에 연결하지 못했습니다.",
                refused=True,
                mode="error",
                citations=[],
            )
            return
        chunks = format_reply(result, repo=self.ctx.memory.repo)
        await status.edit_text(chunks[0])
        for extra in chunks[1:]:
            await message.reply_text(extra)
        self._log_turn(
            user_id=user_id,
            chat_id=chat_id,
            question=question,
            answer=result.answer,
            refused=bool(result.refused) or result.answer.startswith(
                "메모리에 근거가 없어 답할 수 없습니다"
            ),
            mode=result.mode,
            citations=[]
            if result.answer.startswith("메모리에 근거가 없어 답할 수 없습니다")
            else result.citations,
        )

    def _log_turn(
        self,
        *,
        user_id: int,
        chat_id: int,
        question: str,
        answer: str,
        refused: bool,
        mode: str,
        citations: list,
    ) -> None:
        try:
            self.ctx.store.add_turn(
                telegram_user_id=user_id,
                telegram_chat_id=chat_id,
                question=question,
                answer=answer,
                refused=refused,
                mode=mode,
                citations=citations,
            )
        except Exception:
            logger.exception("chat log write failed")

    async def _gate(self, update: Update) -> bool:
        if auth_ok(update, self.auth):
            return True
        user = update.effective_user
        chat = update.effective_chat
        logger.warning(
            "unauthorized telegram request user_id=%s chat_id=%s",
            getattr(user, "id", None),
            getattr(chat, "id", None),
        )
        if update.callback_query:
            await update.callback_query.answer("권한이 없습니다.", show_alert=False)
        elif update.effective_message:
            await update.effective_message.reply_text("사용할 수 없는 계정입니다.")
        return False
