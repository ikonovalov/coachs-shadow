import logging
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.messenger import Messenger
from bot.telegram.states import Picker
from bot.core.errors import DomainError
from bot.telegram.ui import markup, restore_form, safe_edit, take_form
from bot.users.models import User
from bot.users.service import UserService

log = logging.getLogger(__name__)

router = Router()

CANCEL = [("✖️ Отмена", "pk:cancel")]


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
    await message.answer(f"{prompt}\nНапишите часть имени.", reply_markup=markup(CANCEL))
    if isinstance(target, CallbackQuery):
        await target.answer()


@router.message(Picker.query, F.text, ~F.text.startswith("/"))
async def on_query(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    roles = set(data["roles"]) if data.get("roles") else None
    found = await UserService(session).search(message.text, roles=roles)
    if not found:
        await message.answer("Никого не нашёл. Попробуйте другое имя.", reply_markup=markup(CANCEL))
        return
    rows = [[(u.name, f"pk:{u.telegram_id}")] for u in found]
    rows.append([("✖️ Отмена", "pk:cancel")])
    await message.answer("Выберите:", reply_markup=markup(*rows))


@router.callback_query(Picker.query, F.data == "pk:cancel")
async def on_pick_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(cb.message, "Отменено")
    await cb.answer()


@router.callback_query(Picker.query, F.data.startswith("pk:"))
async def on_picked(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger
) -> None:
    data = await take_form(state, Picker.query)  # двойное нажатие: второй раз данных уже нет
    if data is None:
        await cb.answer()
        return
    handler = PICK_HANDLERS.get(data.get("purpose"))
    if handler is None:
        log.error("no pick handler for purpose %r", data.get("purpose"))
        await cb.answer()
        return
    try:
        await handler(cb, session, user, messenger, data["ctx"], int(cb.data.split(":", 1)[1]))
    except DomainError:
        await restore_form(state, Picker.query, data)  # можно выбрать другого
        raise


@router.callback_query(F.data.startswith("pk:"))
async def on_stale_pick(cb: CallbackQuery) -> None:
    await cb.answer()
