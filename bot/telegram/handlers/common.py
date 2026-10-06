from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.messenger import Messenger
from bot.core.permissions import require_admin
from bot.telegram.handlers.inventory import begin_inventory
from bot.telegram.handlers.my import send_my
from bot.telegram.handlers.profile import send_profile
from bot.telegram.handlers.schedule import send_schedule
from bot.telegram.handlers.trainer import ensure_trainer
from bot.telegram.menu import (
    ADMIN_MENU, MENU_ADMIN, MENU_BACK, MENU_MY, MENU_PROFILE, MENU_SCHEDULE, MENU_TRAINER, TRAINER_MENU, main_menu,
)
from bot.telegram.ui import safe_edit
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()
MENU_TITLE = "Выберите действие:"


@router.message(CommandStart(deep_link=True), F.chat.type == "private")
async def cmd_start_link(
    message: Message, command: CommandObject, session: AsyncSession, user: User, state: FSMContext
) -> None:
    arg = command.args or ""
    if arg.startswith("trainer_"):
        await RoleService(session).redeem_invite(arg[len("trainer_"):], user, utcnow())
        await message.answer(
            "Вы назначены тренером. Кнопка «🎓 Тренер» откроется, когда администратор добавит вас в чат клуба.",
            reply_markup=main_menu(user.role),
        )
        return
    if arg.startswith("inv_") and arg[4:].isdigit() and arg[4:].isascii():  # «🚲 Инвентарь» из группы
        await begin_inventory(message, state, session, user, int(arg[4:]))
        return
    await cmd_start(message, user)


@router.message(CommandStart(), F.chat.type == "private")
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(f"Привет, {user.name}! Я бот велоклуба.\n{MENU_TITLE}", reply_markup=main_menu(user.role))


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


@router.callback_query(F.data == MENU_BACK, F.message.chat.type == "private")
async def on_menu_back(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(cb.message, MENU_TITLE, reply_markup=main_menu(user.role))
    await cb.answer()


@router.callback_query(F.data == MENU_SCHEDULE, F.message.chat.type == "private")
async def on_menu_schedule(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, state: FSMContext) -> None:
    await state.clear()
    await send_schedule(cb.message, session, messenger)
    await cb.answer()


@router.callback_query(F.data == MENU_MY, F.message.chat.type == "private")
async def on_menu_my(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await state.clear()
    await send_my(cb.message, session, user)
    await cb.answer()


@router.callback_query(F.data == MENU_PROFILE, F.message.chat.type == "private")
async def on_menu_profile(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    await state.clear()
    await send_profile(cb.message, user)
    await cb.answer()


@router.callback_query(F.data == MENU_TRAINER, F.message.chat.type == "private")
async def on_menu_trainer(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)  # роль проверяется на сервере: кнопка могла устареть
    await state.clear()
    await safe_edit(cb.message, "Меню тренера", reply_markup=TRAINER_MENU)
    await cb.answer()


@router.callback_query(F.data == MENU_ADMIN, F.message.chat.type == "private")
async def on_menu_admin(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    await safe_edit(cb.message, "Меню администратора", reply_markup=ADMIN_MENU)
    await cb.answer()


@router.callback_query(F.data.startswith("menu:"))
async def on_menu_stale(cb: CallbackQuery) -> None:
    await cb.answer()
