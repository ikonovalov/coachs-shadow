import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.views import BillingViews
from bot.core.db import utcnow
from bot.core.errors import NotFound, PermissionDenied
from bot.core.messenger import Messenger
from bot.schedule.models import Training
from bot.schedule.queries import can_manage
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.ui import club_tz, markup, safe_edit, short_title
from bot.usecases import set_attendance
from bot.users.models import User

log = logging.getLogger(__name__)

router = Router()
router.callback_query.filter(F.message.chat.type == "private")


async def _managed(session: AsyncSession, user: User, training_id: int) -> Training:
    training = await session.get(Training, training_id)
    if training is None:
        raise NotFound("Тренировка не найдена")
    if not await can_manage(session, user, training):
        raise PermissionDenied("Нет прав на эту тренировку")
    return training


async def render_roster(session: AsyncSession, training: Training) -> tuple[str, InlineKeyboardMarkup]:
    roster = await BillingViews(session).roster(training.id)
    rows = [
        [(f"{'✅' if present else '❌'} {u.name}", f"att:t:{training.id}:{u.telegram_id}:{0 if present else 1}")]
        for u, present in roster
    ]
    rows += [[("➕ Пришёл без записи", f"att:add:{training.id}")], [("Готово", "att:done")]]
    tz = await club_tz(session)
    return f"Посещение: {short_title(training, tz)}\nНажмите на имя, чтобы изменить отметку.", markup(*rows)


@router.callback_query(F.data.regexp(r"^att:o:\d+$"))
async def on_open(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    training = await _managed(session, user, int(cb.data.split(":")[2]))
    text, keyboard = await render_roster(session, training)
    await safe_edit(cb.message, text, reply_markup=keyboard)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^att:t:\d+:\d+:[01]$"))
async def on_toggle(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    _, _, tid, uid, target = cb.data.split(":")
    training = await _managed(session, user, int(tid))
    result = await set_attendance(session, messenger, user, training.id, int(uid), target == "1", utcnow())
    text, keyboard = await render_roster(session, training)
    await safe_edit(cb.message, text, reply_markup=keyboard)
    await cb.answer("Отмечено" if result.changed else None)


@router.callback_query(F.data.regexp(r"^att:add:\d+$"))
async def on_add(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    training = await _managed(session, user, int(cb.data.split(":")[2]))
    await start_picker(cb, state, purpose="att_add", ctx={"tid": training.id}, prompt="Кто пришёл без записи?")


@on_pick("att_add")
async def picked_walk_in(
    cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int
) -> None:
    training = await _managed(session, user, ctx["tid"])  # выбранные данные подделываемы: права проверяем снова
    await set_attendance(session, messenger, user, training.id, picked_id, True, utcnow())
    text, keyboard = await render_roster(session, training)
    await cb.message.answer(text, reply_markup=keyboard)
    await cb.answer("Отмечено")


@router.callback_query(F.data == "att:done")
async def on_done(cb: CallbackQuery) -> None:
    await safe_edit(cb.message, "Отметка посещения сохранена")
    await cb.answer()


@router.callback_query(F.data.startswith("att:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer("Кнопка устарела", show_alert=True)
