from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import NotFound
from bot.core.permissions import require_admin
from bot.schedule.service import ScheduleService
from bot.telegram.menu import BIND_PREFIX, levels_keyboard
from bot.telegram.ui import safe_edit
from bot.users.models import User

router = Router()  # групповые чаты: фильтра «только личка» здесь нет
GROUPS = {"group", "supergroup"}
NO_LEVELS_HINT = "Уровней пока нет. Сначала создайте уровень в личном чате с ботом (⚙️ Админ → 🎚 Уровни)."


@router.message(Command("bind_level"), F.chat.type.in_(GROUPS))
async def cmd_bind_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    service = ScheduleService(session)
    if command.args:
        await service.bind_chat(user, message.chat.id, message.chat.title or "", command.args)  # права — в сервисе
        await message.answer(f"Чат привязан к уровню «{command.args.strip()}»")
        return
    require_admin(user)  # список уровней в группе показываем только админу
    levels = await service.list_levels()
    if not levels:
        await message.answer(NO_LEVELS_HINT)
        return
    await message.answer("К какому уровню привязать этот чат?", reply_markup=levels_keyboard(levels))


@router.callback_query(F.data.startswith(BIND_PREFIX), F.message.chat.type.in_(GROUPS))
async def on_bind(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    service = ScheduleService(session)
    try:
        level_id = int(cb.data[len(BIND_PREFIX):])
    except ValueError:
        raise NotFound("Уровень не найден") from None
    level = await service.get_level(level_id)
    await service.bind_chat(user, cb.message.chat.id, cb.message.chat.title or "", level.name)
    await safe_edit(cb.message, f"Чат привязан к уровню «{level.name}»")
    await cb.answer()


@router.callback_query(F.data.startswith(BIND_PREFIX))
async def on_bind_stale(cb: CallbackQuery) -> None:
    await cb.answer()  # catch-all роутера: кнопка привязки вне группы
