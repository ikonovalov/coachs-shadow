from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.errors import NotFound, PermissionDenied
from bot.core.messenger import Messenger
from bot.core.permissions import Role
from bot.schedule.models import Chat
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.ui import markup, safe_edit
from bot.users.models import User
from bot.users.roles import RoleService

router = Router()
router.callback_query.filter(F.message.chat.type == "private")  # chat menus live in private only


def _back(user: User) -> tuple[str, str]:
    return ("⬅️ Меню", "ad:menu" if user.role == Role.ADMIN else "tr:menu")


async def _require_manageable(session: AsyncSession, user: User, chat_id: int) -> None:
    if chat_id not in {c.chat_id for c in await RoleService(session).manageable_chats(user)}:
        raise PermissionDenied("Только администратор или главный тренер чата")


async def _show_chat(cb: CallbackQuery, session: AsyncSession, user: User, chat_id: int) -> None:
    svc = RoleService(session)
    chat = await session.get(Chat, chat_id)
    if chat is None:
        raise NotFound("Чат не найден")
    await _require_manageable(session, user, chat_id)
    rows = []
    lines = [f"Чат «{chat.title}»", "Тренеры:"]
    for trainer, is_lead in await svc.chat_trainers(chat_id):
        lines.append(f"{'⭐ ' if is_lead else '• '}{trainer.name}")
        row = [(f"❌ {trainer.name}", f"ch:rm:{chat_id}:{trainer.telegram_id}")]
        if user.role == Role.ADMIN and not is_lead:
            row.append(("⭐ Главным", f"ch:lead:{chat_id}:{trainer.telegram_id}"))
        rows.append(row)
    if len(lines) == 2:
        lines.append("пока нет")
    rows.append([("➕ Добавить тренера", f"ch:add:{chat_id}")])
    rows.append([("⬅️ К чатам", "ch:list")])
    await safe_edit(cb.message, "\n".join(lines), reply_markup=markup(*rows))


async def _show_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    chats = await RoleService(session).manageable_chats(user)
    if not chats:
        await safe_edit(cb.message, "Нет чатов под вашим управлением", reply_markup=markup([_back(user)]))
    else:
        rows = [[(c.title, f"ch:c:{c.chat_id}")] for c in chats]
        rows.append([_back(user)])
        await safe_edit(cb.message, "Чаты:", reply_markup=markup(*rows))


@router.callback_query(F.data == "ch:list")
async def on_list(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    """Список чатов под управлением пользователя."""
    await state.clear()
    await _show_list(cb, session, user)
    await cb.answer()


@router.callback_query(F.data.startswith("ch:c:"))
async def on_chat(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Экран чата: его тренеры, назначение главного, добавление и снятие."""
    await _show_chat(cb, session, user, int(cb.data.split(":")[2]))
    await cb.answer()


@router.callback_query(F.data.startswith("ch:rm:"))
async def on_remove(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Запрос подтверждения снятия тренера с чата."""
    _, _, chat_id, uid = cb.data.split(":")
    await _require_manageable(session, user, int(chat_id))
    target = await session.get(User, int(uid))
    chat = await session.get(Chat, int(chat_id))
    if target is None or chat is None:
        raise NotFound("Не найдено")
    await safe_edit(cb.message, 
        f"Убрать {target.name} из тренеров чата «{chat.title}»?",
        reply_markup=markup([("✅ Убрать", f"ch:rmy:{chat_id}:{uid}"), ("✖️ Отмена", f"ch:c:{chat_id}")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("ch:rmy:"))
async def on_remove_confirmed(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Снимает тренера с чата (права проверяет сервис) и перерисовывает экран."""
    _, _, chat_id, uid = cb.data.split(":")
    await RoleService(session).remove_chat_trainer(user, int(chat_id), int(uid))
    try:
        await _show_chat(cb, session, user, int(chat_id))
    except PermissionDenied:  # главный тренер убрал себя и больше не управляет этим чатом
        await _show_list(cb, session, user)
    await cb.answer("Убран")


@router.callback_query(F.data.startswith("ch:lead:"))
async def on_lead(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Назначает главного тренера чата (админ)."""
    _, _, chat_id, uid = cb.data.split(":")
    await RoleService(session).set_lead(user, int(chat_id), int(uid))
    await _show_chat(cb, session, user, int(chat_id))
    await cb.answer("Назначен главным")


@router.callback_query(F.data.startswith("ch:add:"))
async def on_add(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    """Запускает выбор тренера для добавления в чат."""
    chat_id = int(cb.data.split(":")[2])
    await _require_manageable(session, user, chat_id)
    await start_picker(
        cb, state, purpose="chat_trainer_add", ctx={"chat_id": chat_id},
        prompt="Кого добавить тренером чата? (человек уже должен быть тренером)", roles={Role.TRAINER},
    )


@on_pick("chat_trainer_add")
async def picked(
    cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int
) -> None:
    # RoleService re-checks that the actor manages the chat and that the target is a trainer
    """Результат выбора: добавляет тренера в чат (права и роль цели проверяет сервис)."""
    await RoleService(session).add_chat_trainer(user, ctx["chat_id"], picked_id)
    await cb.message.answer("Тренер добавлен ✅", reply_markup=markup([("⬅️ К чатам", "ch:list")]))
    await cb.answer("Добавлен")


@router.callback_query(F.data.startswith("ch:"))
async def on_stale(cb: CallbackQuery) -> None:
    """Catch-all `ch:`: кнопка устарела."""
    await cb.answer()
