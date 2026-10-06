"""Парк инвентаря в меню администратора: список единиц, вывод из парка / возврат, добавление одной строкой.

Префиксы `ad:park`, `ad:pk:*`, `ad:pka`, `ad:pkk:*` пересекаются с catch-all `ad:` роутера admin,
поэтому этот роутер подключается раньше admin.router (bot/main.py)."""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import DomainError, InvalidAction
from bot.core.messenger import Messenger
from bot.core.permissions import require_admin
from bot.inventory.items import ITEM_FORMATS, parse_item_line
from bot.inventory.matching import KIND_LABELS
from bot.inventory.models import InventoryItem
from bot.inventory.service import InventoryService
from bot.telegram.states import ParkInput
from bot.telegram.ui import markup, restore_form, safe_edit, take_form
from bot.usecases import set_item_active
from bot.users.models import User

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")  # меню администратора — только в личке


def describe(item: InventoryItem) -> str:
    detail = {
        "bike": f"рама {item.frame}, рост {item.height_min}–{item.height_max}",
        "pedals": f"{item.pedal_type}",
        "shoes": f"{item.shoe_eu}, шипы {item.cleat_type}",
        "helmet": f"{item.helmet_size}",
    }[item.kind]
    return f"{item.label} · {detail}"


async def park_screen(session: AsyncSession) -> tuple[str, object]:
    items = await InventoryService(session).list_items()
    # в кнопке — целевое состояние, а не «переключить»: повторный тап по старой кнопке ничего не меняет
    rows = [
        [(("✅ " if i.active else "🚫 ") + describe(i), f"ad:pk:{i.id}:{0 if i.active else 1}")] for i in items
    ]
    rows.append([("➕ Добавить единицу", "ad:pka")])
    rows.append([("⬅️ Меню", "ad:menu")])
    text = "Парк инвентаря (нажмите, чтобы вывести из парка / вернуть):" if items else "Парк пуст"
    return text, markup(*rows)


@router.callback_query(F.data == "ad:park")
async def on_park(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    text, kb = await park_screen(session)
    await safe_edit(cb.message, text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^ad:pk:\d+:[01]$"))
async def on_toggle(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    _, _, item_id, active = cb.data.split(":")
    # права — в сервисе; при выводе из парка будущие заказы единицы отменяются, участники получают уведомление
    item, cancelled = await set_item_active(session, messenger, user, int(item_id), active == "1", utcnow())
    log.info("inventory item %s active=%s by %s", item.id, item.active, user.telegram_id)
    text, kb = await park_screen(session)
    await safe_edit(cb.message, text, reply_markup=kb)
    if item.active:
        await cb.answer("Возвращено в парк")
    else:
        await cb.answer("Выведено из парка" + (f". Отменено заказов: {len(cancelled)}" if cancelled else ""))


@router.callback_query(F.data == "ad:pka")
async def on_add(cb: CallbackQuery, user: User) -> None:
    require_admin(user)
    rows = [[(KIND_LABELS[k], f"ad:pkk:{k}")] for k in ITEM_FORMATS]
    await safe_edit(cb.message, "Что добавить?", reply_markup=markup(*rows, [("⬅️ Назад", "ad:park")]))
    await cb.answer()


@router.callback_query(F.data.startswith("ad:pkk:"))
async def on_kind(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    kind = cb.data.split(":", 2)[2]
    if kind not in ITEM_FORMATS:
        raise InvalidAction("Неизвестный вид инвентаря")
    await state.clear()
    await state.set_state(ParkInput.line)
    await state.update_data(kind=kind)
    await cb.message.answer(f"Одной строкой через «;»:\n{ITEM_FORMATS[kind]}")
    await cb.answer()


@router.message(ParkInput.line, F.text, ~F.text.startswith("/"))
async def on_line(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await take_form(state, ParkInput.line, key="kind")  # два сообщения подряд: добавится одна единица
    if data is None:
        return
    try:
        label, params = parse_item_line(data["kind"], message.text)
        item = await InventoryService(session).add_item(user, data["kind"], label, **params)  # права — в сервисе
    except DomainError:
        await restore_form(state, ParkInput.line, data)  # подсказка формата уходит в ответ, ввод можно повторить
        raise
    log.info("inventory item %s (%s) added by %s", item.id, item.kind, user.telegram_id)
    await message.answer(
        f"Добавлено: {describe(item)} ✅",
        reply_markup=markup([("➕ Ещё", f"ad:pkk:{item.kind}"), ("⬅️ К парку", "ad:park")]),
    )


@router.callback_query(F.data.startswith("ad:pk"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer("Кнопка устарела. Откройте парк заново: /admin", show_alert=True)
