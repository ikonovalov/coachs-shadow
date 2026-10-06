from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()


@router.message(CommandStart(deep_link=True), F.chat.type == "private")
async def cmd_start_link(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    arg = command.args or ""
    if arg.startswith("trainer_"):
        await RoleService(session).redeem_invite(arg[len("trainer_"):], user, utcnow())
        await message.answer("Вы назначены тренером. Меню тренера (/trainer) откроется, когда администратор добавит вас в чат клуба.")
        return
    await cmd_start(message, user)


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(f"Привет, {user.name}! Я бот велоклуба.\nБлижайшие тренировки: /schedule")


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")
