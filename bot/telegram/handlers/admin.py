from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import InvalidAction
from bot.schedule.service import ScheduleService
from bot.users.models import User

router = Router()


@router.message(Command("add_level"))
async def cmd_add_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    if not command.args:
        raise InvalidAction("Использование: /add_level Название уровня")
    level = await ScheduleService(session).add_level(user, command.args)
    await message.answer(f"Уровень «{level.name}» добавлен")


@router.message(Command("bind_level"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_bind_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    if not command.args:
        raise InvalidAction("Использование: /bind_level Название уровня")
    await ScheduleService(session).bind_chat(user, message.chat.id, message.chat.title or "", command.args)
    await message.answer(f"Чат привязан к уровню «{command.args.strip()}»")
