from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.views import BillingViews
from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.render import fit_alert, render_participants
from bot.publishing.service import PublishService
from bot.schedule.models import Training
from bot.usecases import decline_training, join_training
from bot.users.models import User

router = Router()

JOIN_TEXT = {
    JoinOutcome.JOINED: "Вы записаны ✅",
    JoinOutcome.ALREADY: "Вы уже записаны",
    JoinOutcome.FULL: "Мест нет",
}


def _training_id(callback: CallbackQuery) -> int:
    return int(callback.data.split(":", 1)[1])


async def _finish(
    callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User, training_id: int, text: str
) -> None:
    """Re-render the card in a private chat (group posts are refreshed by the use case) and answer."""
    message = callback.message
    if message is not None and message.chat.type == "private":
        training = await session.get(Training, training_id)
        if training is not None:
            new_text, keyboard = await PublishService(session, messenger).render(training)
            await messenger.edit_post(message.chat.id, message.message_id, new_text, keyboard)
    if not user.started_bot:
        username = (await callback.bot.me()).username
        await callback.answer(f"{text}\nОткройте бота, чтобы получать напоминания: t.me/{username}", show_alert=True)
    else:
        await callback.answer(text)


@router.callback_query(F.data.startswith("join:"))
async def on_join(
    callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User, billing_enabled: bool = False
) -> None:
    training_id = _training_id(callback)
    outcome = await join_training(session, messenger, training_id, user.telegram_id, utcnow())
    text = JOIN_TEXT[outcome]
    if billing_enabled and outcome == JoinOutcome.JOINED:
        hint = await BillingViews(session).payment_hint(training_id, user.telegram_id, utcnow())
        if hint:
            text = fit_alert(f"{text}\n{hint}")
    await _finish(callback, session, messenger, user, training_id, text)


@router.callback_query(F.data.startswith("decline:"))
async def on_decline(callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    training_id = _training_id(callback)
    await decline_training(session, messenger, training_id, user.telegram_id, utcnow())
    await _finish(callback, session, messenger, user, training_id, "Записали: не идёте")


@router.callback_query(F.data.startswith("who:"))
async def on_who(callback: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    going, declined = await ParticipationService(session).list_participants(_training_id(callback))
    text = render_participants([u.name for u in going], [u.name for u in declined])
    if len(text) <= 190:
        await callback.answer(text, show_alert=True)
    elif user.started_bot and await messenger.notify(user.telegram_id, text):
        await callback.answer("Полный список отправил вам в личные сообщения")
    else:
        await callback.answer(fit_alert(text), show_alert=True)
