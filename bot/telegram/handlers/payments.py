from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.render import render_my_billing
from bot.billing.views import BillingViews
from bot.core.db import utcnow
from bot.telegram.menu import BACK_ROW
from bot.telegram.ui import club_tz, markup
from bot.users.models import User

router = Router()
router.message.filter(F.chat.type == "private")  # колбэков у экрана нет: возврат — общий menu:back


async def send_payments(message: Message, session: AsyncSession, user: User) -> None:
    view = await BillingViews(session).my_overview(user.telegram_id, utcnow())
    await message.answer(render_my_billing(view, await club_tz(session)), reply_markup=markup(BACK_ROW))


@router.message(Command("payments"))
async def cmd_payments(message: Message, session: AsyncSession, user: User) -> None:
    await send_payments(message, session, user)
