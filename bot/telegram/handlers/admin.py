from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import InvalidAction, NotFound
from bot.core.messenger import Messenger
from bot.core.permissions import Role, require_admin
from bot.core.settings import get_setting, set_setting, validate_setting
from bot.schedule.models import Level
from bot.schedule.service import ScheduleService
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.states import AdminInput
from bot.telegram.ui import markup
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()
router.callback_query.filter(F.message.chat.type == "private")  # admin menus live in private only


ADMIN_MENU = markup(
    [("👥 Тренеры", "ad:trainers"), ("🛡 Админы", "ad:admins")],
    [("🎚 Уровни", "ad:levels"), ("💬 Чаты", "ch:list")],
    [("🔗 Приглашение тренера", "ad:invite"), ("⚙️ Настройки", "ad:settings")],
)
SETTING_LABELS = {
    "publish_window_h": "За сколько часов публиковать пост",
    "reminder_h": "За сколько часов напоминать в личку",
    "duration_min": "Длительность по умолчанию, мин",
    "club_timezone": "Часовой пояс клуба",
}


@router.message(Command("add_level"), F.chat.type == "private")
async def cmd_add_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    if not command.args:
        raise InvalidAction("Использование: /add_level Название уровня")
    level = await ScheduleService(session).add_level(user, command.args)
    await message.answer(f"Уровень «{level.name}» добавлен")


@router.message(Command("bind_level"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_bind_level(message: Message, command: CommandObject, session: AsyncSession, user: User) -> None:
    if not command.args:
        raise InvalidAction("Использование: /bind_level Название уровня")
    await ScheduleService(session).bind_chat(user, message.chat.id, message.chat.title or "", command.args)
    await message.answer(f"Чат привязан к уровню «{command.args.strip()}»")


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    await message.answer("Меню администратора", reply_markup=ADMIN_MENU)


@router.callback_query(F.data == "ad:menu")
async def on_menu(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    await cb.message.edit_text("Меню администратора", reply_markup=ADMIN_MENU)
    await cb.answer()


# ---------- тренеры ----------

async def _show_trainers(cb: CallbackQuery, session: AsyncSession) -> None:
    trainers = await RoleService(session).list_by_role(Role.TRAINER)
    rows = [[(f"❌ {t.name}", f"ad:rt:{t.telegram_id}")] for t in trainers]
    rows.append([("➕ Назначить тренера", "ad:addt")])
    rows.append([("⬅️ Меню", "ad:menu")])
    text = "Тренеры (нажмите, чтобы снять роль):" if trainers else "Тренеров пока нет"
    await cb.message.edit_text(text, reply_markup=markup(*rows))


@router.callback_query(F.data == "ad:trainers")
async def on_trainers(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_trainers(cb, session)
    await cb.answer()


@router.callback_query(F.data.startswith("ad:rt:"))
async def on_revoke_trainer(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    uid = int(cb.data.split(":")[2])
    target = await session.get(User, uid)
    if target is None:
        raise NotFound("Пользователь не найден")
    await cb.message.edit_text(
        f"Снять роль тренера с {target.name}? Он будет убран из тренеров всех чатов.",
        reply_markup=markup([("✅ Снять", f"ad:rty:{uid}"), ("✖️ Отмена", "ad:trainers")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ad:rty:"))
async def on_revoke_trainer_confirmed(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await RoleService(session).revoke_trainer(user, int(cb.data.split(":")[2]))
    await _show_trainers(cb, session)
    await cb.answer("Роль снята")


@router.callback_query(F.data == "ad:addt")
async def on_add_trainer(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await start_picker(cb, state, purpose="grant_trainer", ctx={}, prompt="Кого назначить тренером?", roles={Role.MEMBER})


@on_pick("grant_trainer")
async def picked_trainer(
    cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int
) -> None:
    await RoleService(session).grant_trainer(user, picked_id)  # re-checks actor is admin
    await cb.message.answer(
        "Назначен. Добавьте его в чат: 💬 Чаты", reply_markup=markup([("⬅️ Меню", "ad:menu")])
    )
    await cb.answer("Назначен")


# ---------- админы ----------

async def _show_admins(cb: CallbackQuery, session: AsyncSession) -> None:
    admins = await RoleService(session).list_by_role(Role.ADMIN)
    rows = [[(f"❌ {a.name}", f"ad:ra:{a.telegram_id}")] for a in admins]
    rows.append([("➕ Назначить админа", "ad:adda")])
    rows.append([("⬅️ Меню", "ad:menu")])
    await cb.message.edit_text("Администраторы (нажмите, чтобы снять):", reply_markup=markup(*rows))


@router.callback_query(F.data == "ad:admins")
async def on_admins(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_admins(cb, session)
    await cb.answer()


@router.callback_query(F.data.startswith("ad:ra:"))
async def on_revoke_admin(cb: CallbackQuery, session: AsyncSession, user: User, admin_ids: list[int]) -> None:
    require_admin(user)
    uid = int(cb.data.split(":")[2])
    if uid in admin_ids:
        raise InvalidAction("Администратора из настроек (.env) нельзя снять через бота")
    target = await session.get(User, uid)
    if target is None:
        raise NotFound("Пользователь не найден")
    await cb.message.edit_text(
        f"Снять права администратора с {target.name}?",
        reply_markup=markup([("✅ Снять", f"ad:ray:{uid}"), ("✖️ Отмена", "ad:admins")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ad:ray:"))
async def on_revoke_admin_confirmed(
    cb: CallbackQuery, session: AsyncSession, user: User, admin_ids: list[int]
) -> None:
    await RoleService(session, admin_ids).revoke_admin(user, int(cb.data.split(":")[2]))
    await _show_admins(cb, session)
    await cb.answer("Роль снята")


@router.callback_query(F.data == "ad:adda")
async def on_add_admin(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await start_picker(
        cb, state, purpose="grant_admin", ctx={}, prompt="Кого назначить администратором?",
        roles={Role.MEMBER, Role.TRAINER},
    )


@on_pick("grant_admin")
async def picked_admin(
    cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int
) -> None:
    await RoleService(session).grant_admin(user, picked_id)  # re-checks actor is admin
    await cb.message.answer("Администратор назначен ✅", reply_markup=markup([("⬅️ Меню", "ad:menu")]))
    await cb.answer("Назначен")


# ---------- уровни ----------

@router.callback_query(F.data == "ad:levels")
async def on_levels(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    names = list(await session.scalars(select(Level.name).order_by(Level.position, Level.id)))
    await cb.message.edit_text(
        "Уровни:\n" + ("\n".join(f"• {n}" for n in names) or "пока нет"),
        reply_markup=markup([("➕ Добавить уровень", "ad:addlvl")], [("⬅️ Меню", "ad:menu")]),
    )
    await cb.answer()


@router.callback_query(F.data == "ad:addlvl")
async def on_add_level(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await state.clear()
    await state.set_state(AdminInput.value)
    await state.update_data(purpose="add_level")
    await cb.message.answer("Название нового уровня:")
    await cb.answer()


# ---------- приглашение ----------

@router.callback_query(F.data == "ad:invite")
async def on_invite(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    invite = await RoleService(session).create_invite(user, utcnow())
    username = (await cb.bot.me()).username
    await cb.message.answer(
        "Одноразовая ссылка для тренера (действует 24 часа):\n"
        f"https://t.me/{username}?start=trainer_{invite.token}"
    )
    await cb.answer()


# ---------- настройки ----------

@router.callback_query(F.data == "ad:settings")
async def on_settings(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    rows = []
    for key, label in SETTING_LABELS.items():
        rows.append([(f"{label}: {await get_setting(session, key)}", f"ad:set:{key}")])
    rows.append([("⬅️ Меню", "ad:menu")])
    await cb.message.edit_text("Настройки (нажмите, чтобы изменить):", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("ad:set:"))
async def on_setting(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    key = cb.data.split(":", 2)[2]
    if key not in SETTING_LABELS:
        raise InvalidAction("Неизвестная настройка")
    await state.clear()
    await state.set_state(AdminInput.value)
    await state.update_data(purpose=f"setting:{key}")
    await cb.message.answer(f"{SETTING_LABELS[key]} — новое значение:")
    await cb.answer()


@router.message(AdminInput.value, F.text, ~F.text.startswith("/"), F.chat.type == "private")
async def on_admin_input(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    purpose = (await state.get_data())["purpose"]
    if purpose == "add_level":
        level = await ScheduleService(session).add_level(user, message.text)
        await message.answer(f"Уровень «{level.name}» добавлен. Привяжите чат: /bind_level {level.name}")
    else:
        key = purpose.split(":", 1)[1]
        await set_setting(session, key, validate_setting(key, message.text))  # при ошибке состояние остаётся
        await message.answer("Сохранено ✅")
    await state.clear()


@router.callback_query(F.data.startswith("ad:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer()
