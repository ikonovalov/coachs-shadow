from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.telegram.states import Picker
from bot.telegram.ui import markup
from bot.users.models import User
from bot.users.service import UserService

router = Router()

PickHandler = Callable[[CallbackQuery, AsyncSession, User, Messenger, dict, int], Awaitable[None]]
PICK_HANDLERS: dict[str, PickHandler] = {}


def on_pick(purpose: str):
    def decorator(func: PickHandler) -> PickHandler:
        PICK_HANDLERS[purpose] = func
        return func

    return decorator


async def start_picker(
    target: Message | CallbackQuery,
    state: FSMContext,
    *,
    purpose: str,
    ctx: dict,
    prompt: str,
    roles: set[str] | None = None,
) -> None:
    await state.clear()
    await state.set_state(Picker.query)
    await state.update_data(purpose=purpose, ctx=ctx, roles=sorted(roles) if roles else None)
    message = target.message if isinstance(target, CallbackQuery) else target
    await message.answer(f"{prompt}\nНапишите часть имени.")
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.message(Picker.query, F.text)
async def on_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    roles = set(data["roles"]) if data.get("roles") else None
    found = await UserService(session).search(message.text, roles=roles)
    if not found:
        await message.answer("Никого не нашёл. Попробуйте другое имя.")
        return
    rows = [[(u.name, f"pk:{u.telegram_id}")] for u in found]
    rows.append([("✖️ Отмена", "pk:cancel")])
    await message.answer("Выберите:", reply_markup=markup(*rows))


@router.callback_query(Picker.query, F.data == "pk:cancel")
async def on_pick_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Отменено")
    await cb.answer()


@router.callback_query(Picker.query, F.data.startswith("pk:"))
async def on_picked(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger
) -> None:
    data = await state.get_data()
    await state.clear()
    handler = PICK_HANDLERS[data["purpose"]]
    await handler(cb, session, user, messenger, data["ctx"], int(cb.data.split(":", 1)[1]))
