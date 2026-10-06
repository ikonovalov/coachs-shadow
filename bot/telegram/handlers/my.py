import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.inventory.flow import MyEntry, MyOrder, cancel_buttons, render_my
from bot.inventory.service import InventoryService
from bot.participation.models import ParticipationStatus
from bot.participation.service import ParticipationService
from bot.telegram.ui import club_tz, markup, safe_edit, short_title
from bot.users.models import User

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")  # «Мои записи» — только в личке


async def _render(session: AsyncSession, user: User) -> tuple[str, InlineKeyboardMarkup | None]:
    now = utcnow()
    tz = await club_tz(session)
    views = await InventoryService(session).my_orders(user.telegram_id, now)
    orders_by_training: dict[int, list[str]] = {}
    for view in views:
        orders_by_training.setdefault(view.training.id, []).append(view.item.label)
    upcoming = await ParticipationService(session).my_upcoming(user.telegram_id, now)  # не больше 15
    entries = [
        MyEntry(
            short_title(t, tz),
            "идёте" if status == ParticipationStatus.GOING else "не идёте",
            orders_by_training.get(t.id, []),
        )
        for t, status in upcoming
    ]
    orders = [MyOrder(v.order.id, v.training.id, v.training.starts_at, v.item.label) for v in views]
    # кнопки — только для заказов на тренировки из текста, чтобы у каждой была своя строка
    rows = [[button] for button in cancel_buttons(orders, {t.id for t, _ in upcoming}, tz)]
    return render_my(entries), markup(*rows) if rows else None


async def send_my(message: Message, session: AsyncSession, user: User) -> None:
    text, keyboard = await _render(session, user)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("my"))
async def cmd_my(message: Message, session: AsyncSession, user: User) -> None:
    await send_my(message, session, user)


@router.callback_query(F.data.regexp(r"^my:co:\d+$"))
async def on_cancel_order(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    order_id = int(cb.data.split(":")[2])
    # владельца, время старта и повторное нажатие (условный UPDATE) проверяет сервис
    await InventoryService(session).cancel_order(order_id, user.telegram_id, utcnow())
    log.info("inventory order %s cancelled by %s", order_id, user.telegram_id)
    text, keyboard = await _render(session, user)
    await safe_edit(cb.message, text, reply_markup=keyboard)
    await cb.answer("Заказ отменён")


@router.callback_query(F.data.startswith("my:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer("Кнопка устарела. Обновите список: /my", show_alert=True)
