import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import DomainError, InvalidAction, NotFound, PermissionDenied
from bot.core.messenger import Messenger
from bot.core.permissions import Actor, Role
from bot.participation.service import JoinOutcome, ParticipationService
from bot.publishing.render import render_participants
from bot.publishing.service import PublishService
from bot.schedule.forms import (
    SERIES_FIELD_PROMPTS, TRAINING_FIELD_PROMPTS, apply_series_field, apply_training_field,
)
from bot.schedule.models import Training, TrainingSeries
from bot.schedule.queries import trainer_level_ids
from bot.schedule.service import ScheduleService, series_to_input
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.states import EditField
from bot.telegram.ui import club_tz, local_today, markup, restore_form, safe_edit, short_title, take_form
from bot.usecases import (
    cancel_trainings, manual_add_participant, manual_remove_participant, update_series, update_training,
)
from bot.users.models import User

log = logging.getLogger(__name__)

router = Router()

TRAINER_MENU = markup(
    [("📋 Мои тренировки", "tr:list")],
    [("➕ Новая серия", "tr:new:series"), ("➕ Разовая", "tr:new:one")],
    [("💬 Мои чаты", "ch:list")],
)


async def ensure_trainer(session: AsyncSession, user: User) -> None:
    if user.role == Role.ADMIN:
        return
    if user.role != Role.TRAINER:
        raise PermissionDenied("Раздел для тренеров")
    if not await trainer_level_ids(session, user.telegram_id):
        raise PermissionDenied("Вы тренер, но пока не добавлены ни в один чат. Обратитесь к администратору")


def can_edit_series(actor: Actor, series: TrainingSeries) -> bool:
    """Same rule as ScheduleService.update_series: only the series owner or an admin."""
    return actor.role == Role.ADMIN or series.trainer_id == actor.telegram_id


async def _get_training(session: AsyncSession, training_id: int) -> Training:
    training = await session.get(Training, training_id)
    if training is None:
        raise NotFound("Тренировка не найдена")
    return training


async def _get_managed(session: AsyncSession, user: User, training_id: int) -> Training:
    """Training the actor may manage; used before showing anything about it."""
    training = await _get_training(session, training_id)
    if not await ScheduleService(session).can_manage(user, training):
        raise PermissionDenied("Нет прав на эту тренировку")
    return training


async def _get_editable_series(session: AsyncSession, user: User, series_id: int) -> TrainingSeries:
    series = await session.get(TrainingSeries, series_id)
    if series is None:
        raise NotFound("Серия не найдена")
    if not can_edit_series(user, series):
        raise PermissionDenied("Серию может менять только её тренер или администратор")
    return series


def _id(cb: CallbackQuery, index: int = 2) -> int:
    return int(cb.data.split(":")[index])


# ---------- меню и список ----------

@router.message(Command("trainer"), F.chat.type == "private")
async def cmd_trainer(message: Message, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await message.answer("Меню тренера", reply_markup=TRAINER_MENU)


@router.callback_query(F.data == "tr:menu")
async def on_menu(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await safe_edit(cb.message, "Меню тренера", reply_markup=TRAINER_MENU)
    await cb.answer()


@router.callback_query(F.data == "tr:list")
async def on_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await ensure_trainer(session, user)
    items = await ScheduleService(session).managed_trainings(user, utcnow())
    tz = await club_tz(session)
    rows = [[(short_title(t, tz), f"tr:t:{t.id}")] for t in items]
    if items:
        rows.append([("🗑 Отменить несколько", "tr:multi")])
    rows.append([("⬅️ Меню", "tr:menu")])
    await safe_edit(cb.message, "Мои тренировки" if items else "Ближайших тренировок нет", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.startswith("tr:t:"))
async def on_detail(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    await ensure_trainer(session, user)
    training = await _get_managed(session, user, _id(cb))
    text, _ = await PublishService(session, messenger).render(training)
    tid = training.id
    rows = [
        [("🗑 Отменить", f"tr:cancel:{tid}")],
        [("✏️ Место", f"tr:edit:{tid}:place"), ("✏️ Описание", f"tr:edit:{tid}:description"),
         ("✏️ Лимит", f"tr:edit:{tid}:capacity")],
        [("👥 Участники", f"tr:who:{tid}")],
    ]
    series = await session.get(TrainingSeries, training.series_id)
    if series is not None and can_edit_series(user, series):
        rows.append([("🔁 Изменить серию", f"tr:series:{series.id}")])
    rows.append([("⬅️ Назад", "tr:list")])
    await safe_edit(cb.message, text, reply_markup=markup(*rows))
    await cb.answer()


# ---------- отмена ----------

@router.callback_query(F.data.startswith("tr:cancel:"))
async def on_cancel_ask(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    training = await _get_managed(session, user, _id(cb))
    tz = await club_tz(session)
    await safe_edit(cb.message, 
        f"Отменить тренировку?\n{short_title(training, tz)}\nЗаписавшимся уйдёт уведомление.",
        reply_markup=markup([("🗑 Да, отменить", f"tr:cancelok:{training.id}"), ("Нет", f"tr:t:{training.id}")]),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:cancelok:"))
async def on_cancel_do(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    await cancel_trainings(session, messenger, user, [_id(cb)], utcnow())
    await safe_edit(cb.message, "Тренировка отменена", reply_markup=markup([("⬅️ К списку", "tr:list")]))
    await cb.answer()


async def _render_multi(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    selected = set((await state.get_data()).get("sel", []))
    tz = await club_tz(session)
    items = await ScheduleService(session).managed_trainings(user, utcnow(), limit=30)
    rows = [[(("✅ " if t.id in selected else "▫️ ") + short_title(t, tz), f"tr:mt:{t.id}")] for t in items]
    rows.append([(f"Отменить выбранные ({len(selected)})", "tr:mgo")])
    rows.append([("⬅️ Назад", "tr:list")])
    await safe_edit(cb.message, "Отметьте тренировки для отмены", reply_markup=markup(*rows))


@router.callback_query(F.data == "tr:multi")
async def on_multi(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await state.update_data(sel=[])
    await _render_multi(cb, session, user, state)
    await cb.answer()


@router.callback_query(F.data.startswith("tr:mt:"))
async def on_multi_toggle(cb: CallbackQuery, session: AsyncSession, user: User, state: FSMContext) -> None:
    tid = _id(cb)
    sel = set((await state.get_data()).get("sel", []))
    sel.symmetric_difference_update({tid})
    await state.update_data(sel=sorted(sel))
    await _render_multi(cb, session, user, state)
    await cb.answer()


@router.callback_query(F.data == "tr:mgo")
async def on_multi_ask(cb: CallbackQuery, state: FSMContext) -> None:
    sel = (await state.get_data()).get("sel", [])
    if not sel:
        await cb.answer("Ничего не выбрано", show_alert=True)
        return
    await safe_edit(cb.message, 
        f"Отменить тренировок: {len(sel)}? Записавшимся уйдут уведомления.",
        reply_markup=markup([("🗑 Да, отменить", "tr:mok"), ("Нет", "tr:multi")]),
    )
    await cb.answer()


@router.callback_query(F.data == "tr:mok")
async def on_multi_do(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, state: FSMContext) -> None:
    data = await take_form(state, key="sel")  # до сервиса: повторное нажатие ничего не найдёт
    if data is None:
        await cb.answer()
        return
    sel = data["sel"]
    try:
        await cancel_trainings(session, messenger, user, list(sel), utcnow())  # всё или ничего
    except DomainError:
        await restore_form(state, None, data)
        raise
    await safe_edit(cb.message, f"Отменено тренировок: {len(sel)}", reply_markup=markup([("⬅️ К списку", "tr:list")]))
    await cb.answer()


# ---------- правка одной тренировки ----------

@router.callback_query(F.data.startswith("tr:edit:"))
async def on_edit_training(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    _, _, tid, field = cb.data.split(":")
    if field not in TRAINING_FIELD_PROMPTS:
        raise InvalidAction("Это поле нельзя изменить")
    await _get_managed(session, user, int(tid))
    await state.clear()
    await state.set_state(EditField.value)
    await state.update_data(target="training", id=int(tid), field=field)
    await cb.message.answer(TRAINING_FIELD_PROMPTS[field])
    await cb.answer()


# ---------- правка серии ----------

@router.callback_query(F.data.startswith("tr:series:"))
async def on_series_menu(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    sid = _id(cb)
    await _get_editable_series(session, user, sid)
    labels = {"kind": "Тип", "place": "Место", "description": "Описание", "capacity": "Лимит",
              "weekdays": "Дни", "time": "Время", "end_date": "Окончание"}
    rows = [[(f"✏️ {label}", f"tr:sedit:{sid}:{field}")] for field, label in labels.items()]
    rows.append([("⬅️ Назад", "tr:list")])
    await safe_edit(cb.message, 
        "Изменение серии применится ко всем будущим тренировкам. Уже опубликованные будут отменены "
        "и заменены новыми, записи придётся оформить заново.",
        reply_markup=markup(*rows),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:sedit:"))
async def on_edit_series(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    _, _, sid, field = cb.data.split(":")
    if field not in SERIES_FIELD_PROMPTS:
        raise InvalidAction("Это поле нельзя изменить")
    await _get_editable_series(session, user, int(sid))
    await state.clear()
    await state.set_state(EditField.value)
    await state.update_data(target="series", id=int(sid), field=field)
    await cb.message.answer(SERIES_FIELD_PROMPTS[field])
    await cb.answer()


@router.message(EditField.value, F.text, ~F.text.startswith("/"))
async def on_edit_value(
    message: Message, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger
) -> None:
    data = await state.get_data()
    now = utcnow()
    if data["target"] == "training":
        if await take_form(state, EditField.value) is None:  # второе сообщение подряд: уже обрабатывается
            return
        try:
            edit = apply_training_field(data["field"], message.text)
            await update_training(session, messenger, user, data["id"], edit, now)
        except DomainError:
            await restore_form(state, EditField.value, data)  # ошибка ввода: можно повторить
            raise
        await message.answer("Сохранено ✅", reply_markup=markup([("⬅️ К списку", "tr:list")]))
        return
    series = await _get_editable_series(session, user, data["id"])
    apply_series_field(series_to_input(series), data["field"], message.text, await local_today(session))  # проверка ввода
    await state.update_data(text=message.text)
    await message.answer(
        "Применить к будущим тренировкам серии? Опубликованные будут отменены и заменены.",
        reply_markup=markup([("✅ Применить", "tr:sok"), ("✖️ Отмена", "tr:menu")]),
    )


@router.callback_query(EditField.value, F.data == "tr:sok")
async def on_series_confirm(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, messenger: Messenger) -> None:
    data = await take_form(state, EditField.value, key="text")  # до сервиса: двойное нажатие применит один раз
    if data is None:
        await cb.answer("Сначала введите значение")
        return
    try:
        series = await _get_editable_series(session, user, data["id"])
        new = apply_series_field(series_to_input(series), data["field"], data["text"], await local_today(session))
        replaced = await update_series(session, messenger, user, series.id, new, utcnow())
    except DomainError:
        await restore_form(state, EditField.value, data)
        raise
    await safe_edit(cb.message, 
        f"Серия обновлена ✅ Заменено опубликованных тренировок: {len(replaced)}",
        reply_markup=markup([("⬅️ К списку", "tr:list")]),
    )
    await cb.answer()


# ---------- участники ----------

async def _show_participants(cb: CallbackQuery, session: AsyncSession, user: User, training_id: int) -> None:
    await _get_managed(session, user, training_id)
    going, declined = await ParticipationService(session).list_participants(training_id)
    rows = [[(f"❌ {u.name}", f"tr:rm:{training_id}:{u.telegram_id}")] for u in going]
    rows.append([("➕ Записать участника", f"tr:add:{training_id}")])
    rows.append([("⬅️ Назад", f"tr:t:{training_id}")])
    text = render_participants([u.name for u in going], [u.name for u in declined])
    await safe_edit(cb.message, text + "\n\nНажмите на имя, чтобы убрать из списка.", reply_markup=markup(*rows))


@router.callback_query(F.data.startswith("tr:who:"))
async def on_who(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await _show_participants(cb, session, user, _id(cb))
    await cb.answer()


@router.callback_query(F.data.startswith("tr:rm:"))
async def on_remove(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    training_id, user_id = _id(cb), _id(cb, 3)
    await manual_remove_participant(session, messenger, user, training_id, user_id, utcnow())
    await _show_participants(cb, session, user, training_id)
    await cb.answer("Убран из списка")


@router.callback_query(F.data.startswith("tr:add:"))
async def on_add(cb: CallbackQuery, state: FSMContext) -> None:
    await start_picker(cb, state, purpose="manual_add", ctx={"training_id": _id(cb)}, prompt="Кого записать?")


@on_pick("manual_add")
async def picked_for_manual_add(cb, session, user, messenger, ctx, picked_id) -> None:
    training_id = ctx["training_id"]
    outcome = await manual_add_participant(session, messenger, user, training_id, picked_id, utcnow())
    if outcome == JoinOutcome.OVER_LIMIT:
        await safe_edit(cb.message, 
            "⚠️ Лимит превышен. Записать всё равно?",
            reply_markup=markup([("Да, записать", f"tr:force:{training_id}:{picked_id}"), ("Нет", f"tr:who:{training_id}")]),
        )
    else:
        await safe_edit(cb.message, 
            "Уже записан" if outcome == JoinOutcome.ALREADY else "Записан ✅",
            reply_markup=markup([("⬅️ К участникам", f"tr:who:{training_id}")]),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("tr:force:"))
async def on_force(cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger) -> None:
    training_id, user_id = _id(cb), _id(cb, 3)
    await manual_add_participant(session, messenger, user, training_id, user_id, utcnow(), allow_over_limit=True)
    await _show_participants(cb, session, user, training_id)
    await cb.answer("Записан сверх лимита")


@router.callback_query(F.data.startswith("tr:"))
async def on_stale(cb: CallbackQuery) -> None:
    """Last in the router: an expired button (e.g. "Применить" after the form was closed) just stops the spinner."""
    await cb.answer("Кнопка устарела. Откройте меню заново: /trainer")
