from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.users.models import User

router = Router()


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(f"Привет, {user.name}! Я бот велоклуба.\nБлижайшие тренировки: /schedule")


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")
