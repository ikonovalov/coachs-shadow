from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.publishing.service import PublishService
from bot.schedule.service import ScheduleService
from bot.telegram.keyboards import to_markup

router = Router()


async def send_schedule(message: Message, session: AsyncSession, messenger: Messenger) -> None:
    """Общий показ расписания: его зовут и /schedule, и кнопка «📅 Расписание»."""
    trainings = await ScheduleService(session).upcoming(utcnow())
    if not trainings:
        await message.answer("Ближайших тренировок нет")
        return
    publisher = PublishService(session, messenger)
    for training in trainings:
        text, keyboard = await publisher.render(training)
        await message.answer(text, reply_markup=to_markup(keyboard))


@router.message(Command("schedule"), F.chat.type == "private")
async def cmd_schedule(message: Message, session: AsyncSession, messenger: Messenger) -> None:
    await send_schedule(message, session, messenger)


@router.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()
