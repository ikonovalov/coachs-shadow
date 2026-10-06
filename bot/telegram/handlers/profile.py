import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import DomainError
from bot.telegram.states import ProfileInput
from bot.telegram.ui import markup, restore_form, safe_edit, take_form
from bot.users.models import User
from bot.users.profile import CLEAT_TYPES, PROFILE_PROMPTS, render_profile
from bot.users.service import UserService

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")  # профиль — только в личке

SHORT_CLEAT = {"none": "Плоские", "spd": "SPD", "look": "Look", "spd_sl": "SPD-SL"}
HINT = "\n\nШлем и шипы — кнопками, рост и размер ноги — числом."
ASK_FIELDS = {"pf:h": "height_cm", "pf:f": "foot_eu"}
CHOICE_FIELDS = {"m": "helmet_size", "c": "cleat_type"}


def profile_markup():
    return markup(
        [("📏 Рост", "pf:h"), ("🦶 Размер ноги", "pf:f")],
        [("⛑ S", "pf:m:S"), ("⛑ M", "pf:m:M"), ("⛑ L", "pf:m:L")],
        [(SHORT_CLEAT[c], f"pf:c:{c}") for c in CLEAT_TYPES],
    )


async def send_profile(message: Message, user: User) -> None:
    await message.answer(render_profile(user) + HINT, reply_markup=profile_markup())


@router.message(Command("profile"))
async def cmd_profile(message: Message, user: User, state: FSMContext) -> None:
    await state.clear()
    await send_profile(message, user)


@router.callback_query(F.data.in_(set(ASK_FIELDS)))
async def on_ask(cb: CallbackQuery, state: FSMContext) -> None:
    field = ASK_FIELDS[cb.data]
    await state.clear()
    await state.set_state(ProfileInput.value)
    await state.update_data(field=field)
    await cb.message.answer(PROFILE_PROMPTS[field])
    await cb.answer()


@router.message(ProfileInput.value, F.text, ~F.text.startswith("/"))
async def on_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await take_form(state, ProfileInput.value, key="field")
    if data is None:
        return
    try:
        await UserService(session).update_profile(user, data["field"], message.text)
    except DomainError:
        await restore_form(state, ProfileInput.value, data)  # подсказка уходит в ответ, ввод можно повторить
        raise
    log.info("profile %s updated by %s", data["field"], user.telegram_id)
    await message.answer(render_profile(user) + HINT, reply_markup=profile_markup())


@router.callback_query(F.data.regexp(r"^pf:[mc]:[^:]+$"))
async def on_choice(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, kind, value = cb.data.split(":")
    await UserService(session).update_profile(user, CHOICE_FIELDS[kind], value)  # значение проверяет сервис
    await safe_edit(cb.message, render_profile(user) + HINT, reply_markup=profile_markup())
    await cb.answer("Сохранено")


@router.callback_query(F.data.startswith("pf:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer("Кнопка устарела. Откройте профиль заново: /profile", show_alert=True)
