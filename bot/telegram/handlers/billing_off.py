from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

router = Router()


@router.message(Command("payments"), F.chat.type == "private")
async def cmd_payments(message: Message) -> None:
    await message.answer("Оплата отключена")


@router.callback_query(F.data.startswith(("pay:", "att:")))
async def on_callback(cb: CallbackQuery) -> None:
    await cb.answer("Оплата отключена", show_alert=True)
