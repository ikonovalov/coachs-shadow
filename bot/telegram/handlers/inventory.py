"""Заказ инвентаря участником (личка, префиксы `iv:` / `inv:`) и сводка заказов для тренера (`tr:inv:`).

Порядок вопросов (`_advance`): виды → тип педалей → источник туфель (только если педали из парка, а туфли — нет) →
шипы своих туфель → недостающие поля профиля → подбор и обзор. Все проверки (запись, дедлайн, занятость единиц,
совместимость, права тренера) повторяет сервис: данные FSM и callback'ов UI только подсказывают."""
import logging
from dataclasses import asdict

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import DomainError
from bot.inventory.flow import can_confirm, format_review, kinds_markup_data
from bot.inventory.matching import (
    KIND_LABELS, PEDAL_LABELS, PEDAL_TYPES, Profile, Suggestion, Wanted, missing_profile_fields,
)
from bot.inventory.models import InventoryItem, ItemKind
from bot.inventory.service import InventoryService, NotGoing
from bot.inventory.summary import breakdown, summarize
from bot.schedule.models import Training
from bot.telegram.states import InventoryFlow
from bot.telegram.ui import club_tz, markup, restore_form, safe_edit, short_title, take_form
from bot.users.models import User
from bot.users.profile import CLEAT_LABELS, CLEAT_TYPES, HELMET_SIZES, PROFILE_PROMPTS
from bot.users.service import UserService

log = logging.getLogger(__name__)

router = Router()

KINDS_TEXT = "Что нужно на тренировку?"
CANCEL_ROW = [("✖️ Отмена", "iv:no")]
STALE = "Кнопка устарела. Откройте заказ заново кнопкой «🚲 Инвентарь» на карточке тренировки"


def _kinds_markup(selected: set[str]) -> InlineKeyboardMarkup:
    return markup(*[[row] for row in kinds_markup_data(selected)], CANCEL_ROW)


def _ordered_text(confirmed: list[str], not_found: list[str]) -> str:
    text = "✅ Заказано: " + ", ".join(confirmed) + "\nОтменить заказ можно в /my до начала тренировки."
    if not_found:
        text += "\n\nНе нашлось:\n" + "\n".join(not_found)
    return text


# ---------- вход ----------

async def begin_inventory(message: Message, state: FSMContext, session: AsyncSession, user: User, training_id: int) -> None:
    """Старт диалога в личке: из кнопки на карточке (личка) или из диплинка `inv_<id>`."""
    try:
        await InventoryService(session).check_can_order(training_id, user.telegram_id, utcnow())
    except NotGoing as exc:
        await message.answer(exc.message, reply_markup=markup([("✅ Записаться", f"join:{training_id}")]))
        return
    await state.clear()
    await state.set_state(InventoryFlow.kinds)
    await state.update_data(training_id=training_id, kinds=[], confirmed=[], not_found=[], replacement=False)
    await message.answer(KINDS_TEXT, reply_markup=_kinds_markup(set()))


@router.callback_query(F.data.regexp(r"^inv:\d+$"))
async def on_inventory_button(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    training_id = int(cb.data.split(":")[1])
    if cb.message is not None and cb.message.chat.type == "private":
        await begin_inventory(cb.message, state, session, user, training_id)
        await cb.answer()
        return
    username = (await cb.bot.me()).username  # в группе: «Продолжим в личке» по диплинку
    await cb.answer("Продолжим в личке", url=f"https://t.me/{username}?start=inv_{training_id}")


# ---------- виды ----------

@router.callback_query(InventoryFlow.kinds, F.data.regexp(r"^iv:k:[a-z_]+$"))
async def on_kind_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    kind = cb.data.split(":")[2]
    if kind not in KIND_LABELS:
        await cb.answer(STALE, show_alert=True)
        return
    selected = set((await state.get_data()).get("kinds", []))
    selected.symmetric_difference_update({kind})
    await state.update_data(kinds=sorted(selected))
    await safe_edit(cb.message, KINDS_TEXT, reply_markup=_kinds_markup(selected))
    await cb.answer()


@router.callback_query(InventoryFlow.kinds, F.data == "iv:kdone")
async def on_kinds_done(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    if not (await state.get_data()).get("kinds"):
        await cb.answer("Выберите хотя бы одну позицию", show_alert=True)
        return
    await state.update_data(pedal_type=None, shoes_own=None, own_cleat=None)
    await _advance(cb.message, state, session, user)
    await cb.answer()


# ---------- уточнения ----------

async def _advance(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    """Задаёт следующий недостающий вопрос; когда всё известно — подбор и обзор."""
    data = await state.get_data()
    kinds = set(data["kinds"])  # позиции из парка; свои туфли сюда не попадают
    if ItemKind.PEDALS in kinds and not data.get("pedal_type"):
        await state.set_state(InventoryFlow.pedal)
        rows = [[(PEDAL_LABELS[p].capitalize(), f"iv:p:{p}")] for p in PEDAL_TYPES]
        await message.answer("Какие педали?", reply_markup=markup(*rows, CANCEL_ROW))
        return
    # педали из парка без туфель из парка: уточняем, свои ли туфли, чтобы проверить их шипы с педалями
    if ItemKind.PEDALS in kinds and ItemKind.SHOES not in kinds and data.get("shoes_own") is None:
        await state.set_state(InventoryFlow.shoes)
        await message.answer(
            "Туфли свои или из парка?",
            reply_markup=markup([("Свои", "iv:s:own"), ("Из парка", "iv:s:park")], CANCEL_ROW),
        )
        return
    if data.get("shoes_own") and not data.get("own_cleat"):
        await state.set_state(InventoryFlow.cleat)
        rows = [[(CLEAT_LABELS[c], f"iv:c:{c}")] for c in CLEAT_TYPES]
        await message.answer("Какие шипы на ваших туфлях?", reply_markup=markup(*rows, CANCEL_ROW))
        return
    missing = missing_profile_fields(Profile(user.height_cm, user.foot_eu, user.helmet_size), kinds)
    if missing:
        field = missing[0]
        if field == "helmet_size":
            await state.set_state(InventoryFlow.helmet)
            rows = [[(s, f"iv:h:{s}") for s in HELMET_SIZES]]
            await message.answer("Размер шлема?", reply_markup=markup(*rows, CANCEL_ROW))
        else:
            await state.set_state(InventoryFlow.profile)
            await state.update_data(ask=field)
            await message.answer(PROFILE_PROMPTS[field])
        return
    await _review(message, state, session, user, kinds)


async def _check_own_shoes(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Свои туфли не подходят к выбранным педалям — говорим сразу и просим выбрать педали заново."""
    data = await state.get_data()
    problem = await InventoryService(session).own_shoes_problem(data.get("own_cleat"), data.get("pedal_type"))
    if problem:
        await state.update_data(pedal_type=None)
        await message.answer(problem + " Выберите педали заново.")


@router.callback_query(InventoryFlow.pedal, F.data.regexp(r"^iv:p:[a-z_]+$"))
async def on_pedal(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    pedal_type = cb.data.split(":")[2]
    if pedal_type not in PEDAL_TYPES:
        await cb.answer(STALE, show_alert=True)
        return
    await state.update_data(pedal_type=pedal_type)
    await _check_own_shoes(cb.message, state, session)  # шипы своих туфель уже известны при повторном выборе
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.callback_query(InventoryFlow.shoes, F.data.in_({"iv:s:own", "iv:s:park"}))
async def on_shoes_source(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    own = cb.data == "iv:s:own"
    data = await state.get_data()
    kinds = set(data["kinds"])
    if not own:
        kinds.add(ItemKind.SHOES)
    await state.update_data(shoes_own=own, kinds=sorted(kinds))
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.callback_query(InventoryFlow.cleat, F.data.regexp(r"^iv:c:[a-z_]+$"))
async def on_cleat(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    cleat = cb.data.split(":")[2]
    await UserService(session).update_profile(user, "cleat_type", cleat)  # проверяет значение; сохраняется в профиль
    await state.update_data(own_cleat=user.cleat_type)
    await _check_own_shoes(cb.message, state, session)
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.callback_query(InventoryFlow.helmet, F.data.regexp(r"^iv:h:[A-Za-z]+$"))
async def on_helmet(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await UserService(session).update_profile(user, "helmet_size", cb.data.split(":")[2])  # значение проверяет сервис
    await _advance(cb.message, state, session, user)
    await cb.answer()


@router.message(InventoryFlow.profile, F.text, ~F.text.startswith("/"))
async def on_profile_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await take_form(state, InventoryFlow.profile, key="ask")
    if data is None:
        return
    try:
        await UserService(session).update_profile(user, data["ask"], message.text)
    except DomainError:
        await restore_form(state, InventoryFlow.profile, data)  # подсказка уходит в ответ, ввод можно повторить
        raise
    await restore_form(state, InventoryFlow.profile, data)  # диалог продолжается: _advance выставит следующий шаг
    await _advance(message, state, session, user)


@router.message(InventoryFlow.profile)
async def on_profile_not_text(message: Message, state: FSMContext) -> None:
    field = (await state.get_data()).get("ask")
    await message.answer(PROFILE_PROMPTS.get(field, "Ответьте текстом"))


# ---------- обзор и подтверждение ----------

async def _review(message: Message, state: FSMContext, session: AsyncSession, user: User, park_kinds: set[str]) -> None:
    """Подбор по `park_kinds`. При замене это только виды, которые забрали: `pedal_type` в FSM — тип уже
    подтверждённых педалей, поэтому замена туфель подбирается совместимой с ними (сервис сверяет совместимость
    только внутри одного вызова confirm)."""
    data = await state.get_data()
    wanted = Wanted(frozenset(park_kinds), pedal_type=data.get("pedal_type"), own_shoes_cleat=data.get("own_cleat"))
    suggestions = await InventoryService(session).suggest(data["training_id"], user, wanted, utcnow())
    await state.update_data(suggestions=[asdict(s) for s in suggestions])
    await state.set_state(InventoryFlow.review)
    replacement = data.get("replacement", False)
    buttons = [[("✅ Подтвердить", "iv:ok")]] if can_confirm(suggestions) else []
    buttons.append([("Без замены", "iv:no")] if replacement else CANCEL_ROW)
    await message.answer(
        format_review(suggestions, data.get("confirmed", []), replacement), reply_markup=markup(*buttons)
    )


@router.callback_query(F.data == "iv:ok")
async def on_confirm(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await take_form(state, InventoryFlow.review, key="suggestions")  # двойной тап подтвердит один раз
    if data is None:
        await cb.answer(STALE, show_alert=True)
        return
    suggestions = [Suggestion(**d) for d in data["suggestions"]]
    item_ids = [s.item_id for s in suggestions if s.item_id is not None]
    try:
        result = await InventoryService(session).confirm(data["training_id"], user.telegram_id, item_ids, utcnow())
    except DomainError:
        await restore_form(state, InventoryFlow.review, data)
        raise
    confirmed = list(data.get("confirmed", []))
    for order in result.orders:
        confirmed.append((await session.get(InventoryItem, order.item_id)).label)
    not_found = list(data.get("not_found", [])) + [s.text for s in suggestions if s.item_id is None]
    log.info(
        "inventory ordered by %s for training %s: items=%s conflicts=%s",
        user.telegram_id, data["training_id"], [o.item_id for o in result.orders], result.conflicts,
    )
    if result.conflicts:
        # единицу забрали: замена подбирается только для этих видов, подтверждённое сохраняется
        await restore_form(state, InventoryFlow.review, {
            **data, "confirmed": confirmed, "not_found": not_found, "kinds": sorted(result.conflicts),
            "replacement": True, "suggestions": [],
        })
        await _review(cb.message, state, session, user, set(result.conflicts))
        await cb.answer()
        return
    await safe_edit(cb.message, _ordered_text(confirmed, not_found))
    await cb.answer("Заказано")


@router.callback_query(F.data == "iv:no")
async def on_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    # без привязки к состоянию: «Отмена» есть на каждом шаге диалога
    data = await state.get_data()
    await state.clear()
    confirmed = data.get("confirmed") or []
    if confirmed:  # после замены часть позиций уже заказана — это не отмена
        await safe_edit(cb.message, _ordered_text(confirmed, data.get("not_found") or []))
    else:
        await safe_edit(cb.message, "Заказ инвентаря отменён")
    await cb.answer()


# ---------- сводка тренера ----------

@router.callback_query(F.data.regexp(r"^tr:inv:\d+$"))
async def on_trainer_summary(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    training_id = int(cb.data.split(":")[2])
    lines = await InventoryService(session).training_summary(user, training_id)  # права проверяет сервис
    training = await session.get(Training, training_id)
    tz = await club_tz(session)
    text = f"{short_title(training, tz)}\n{summarize(lines)}"
    if lines:
        text += "\n\n" + breakdown(lines)
    await safe_edit(cb.message, text, reply_markup=markup([("⬅️ Назад", f"tr:t:{training_id}")]))
    await cb.answer()


# ---------- устаревшие кнопки ----------

@router.callback_query(F.data.startswith("iv:") | F.data.startswith("inv:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer(STALE, show_alert=True)
