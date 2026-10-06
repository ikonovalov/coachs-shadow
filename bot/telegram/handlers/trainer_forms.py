import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.db import utcnow
from bot.core.errors import InvalidAction
from bot.schedule.forms import (
    build_series_input, parse_capacity, parse_date, parse_kind, parse_optional_date, parse_place,
    parse_time, parse_weekdays, summarize_series,
)
from bot.schedule.models import Level
from bot.schedule.service import ScheduleService
from bot.telegram.handlers.trainer import ensure_trainer
from bot.telegram.states import NewTraining
from bot.telegram.ui import local_today, markup
from bot.users.models import User

log = logging.getLogger(__name__)

router = Router()
router.message.filter(F.chat.type == "private")

KIND_ROW = [("Интервалы", "nf:kind:intervals"), ("Длительная", "nf:kind:endurance"), ("Техника", "nf:kind:technique")]
CANCEL_ROW = [("✖️ Отмена", "nf:no")]
TEXT = (F.text, ~F.text.startswith("/"))


@router.message.outer_middleware()
async def drop_form_on_command(handler, event, data):
    """A command typed during the form leaves it and falls through to the normal handlers."""
    state = data.get("state")
    if state is not None and (event.text or "").startswith("/"):
        current = await state.get_state()
        if current and current.startswith(NewTraining.__name__ + ":"):
            await state.clear()
    return await handler(event, data)


@router.callback_query(F.data.in_({"tr:new:series", "tr:new:one"}))
async def on_new(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await ensure_trainer(session, user)
    await state.clear()
    await state.update_data(one_off=cb.data.endswith(":one"), level_ids=[])
    await state.set_state(NewTraining.kind)
    await cb.message.edit_text("Тип тренировки:", reply_markup=markup(KIND_ROW, CANCEL_ROW))
    await cb.answer()


@router.callback_query(NewTraining.kind, F.data.startswith("nf:kind:"))
async def on_kind(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(kind=parse_kind(cb.data.split(":")[2]))
    await state.set_state(NewTraining.place)
    await cb.message.edit_text("Где проходит тренировка? Напишите место.", reply_markup=markup(CANCEL_ROW))
    await cb.answer()


@router.message(NewTraining.place, *TEXT)
async def on_place(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    place = parse_place(message.text)
    await state.update_data(place=place)
    await state.set_state(NewTraining.levels)
    await message.answer(
        "Для каких уровней? Отметьте и нажмите «Готово».",
        reply_markup=await _levels_markup(session, user, []),
    )


async def _levels_markup(session: AsyncSession, user: User, selected: list[int]):
    levels = await ScheduleService(session).allowed_levels(user)
    rows = [[(("✅ " if lvl.id in selected else "▫️ ") + lvl.name, f"nf:lvl:{lvl.id}")] for lvl in levels]
    rows.append([("Готово", "nf:lvldone")])
    rows.append(CANCEL_ROW)
    return markup(*rows)


@router.callback_query(NewTraining.levels, F.data.startswith("nf:lvl:"))
async def on_level_toggle(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    level_id = int(cb.data.split(":")[2])
    allowed = {lvl.id for lvl in await ScheduleService(session).allowed_levels(user)}
    if level_id not in allowed:
        raise InvalidAction("Этот уровень вам недоступен")
    selected = set((await state.get_data()).get("level_ids", []))
    selected.symmetric_difference_update({level_id})
    await state.update_data(level_ids=sorted(selected))
    await cb.message.edit_reply_markup(reply_markup=await _levels_markup(session, user, sorted(selected)))
    await cb.answer()


@router.callback_query(NewTraining.levels, F.data == "nf:lvldone")
async def on_levels_done(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("level_ids"):
        await cb.answer("Выберите хотя бы один уровень", show_alert=True)
        return
    if data["one_off"]:
        await state.set_state(NewTraining.date)
        await cb.message.answer("Дата тренировки, например 20.10:")
    else:
        await state.set_state(NewTraining.weekdays)
        await cb.message.answer("Дни недели, например: вт чт")
    await cb.answer()


@router.message(NewTraining.weekdays, *TEXT)
async def on_weekdays(message: Message, state: FSMContext, session: AsyncSession) -> None:
    days = parse_weekdays(message.text)
    await state.update_data(weekdays=sorted(days), start_date=(await local_today(session)).isoformat())
    await state.set_state(NewTraining.time)
    await message.answer("Время начала, например 19:00:")


@router.message(NewTraining.date, *TEXT)
async def on_date(message: Message, state: FSMContext, session: AsyncSession) -> None:
    day = parse_date(message.text, await local_today(session))
    await state.update_data(weekdays=[], start_date=day.isoformat())
    await state.set_state(NewTraining.time)
    await message.answer("Время начала, например 19:00:")


@router.message(NewTraining.time, *TEXT)
async def on_time(message: Message, state: FSMContext) -> None:
    await state.update_data(start_time=parse_time(message.text).strftime("%H:%M"))
    if (await state.get_data())["one_off"]:
        await state.set_state(NewTraining.capacity)
        await message.answer("Лимит мест (число) или «-» без лимита:")
    else:
        await state.set_state(NewTraining.end_date)
        await message.answer("Дата окончания серии, например 31.12, или «-» без окончания:")


@router.message(NewTraining.end_date, *TEXT)
async def on_end_date(message: Message, state: FSMContext, session: AsyncSession) -> None:
    end = parse_optional_date(message.text, await local_today(session))
    await state.update_data(end_date=end.isoformat() if end else None)
    await state.set_state(NewTraining.capacity)
    await message.answer("Лимит мест (число) или «-» без лимита:")


@router.message(NewTraining.capacity, *TEXT)
async def on_capacity(message: Message, state: FSMContext) -> None:
    await state.update_data(capacity=parse_capacity(message.text))
    await state.set_state(NewTraining.description)
    await message.answer("Описание (или «-»):")


@router.message(NewTraining.description, *TEXT)
async def on_description(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    await state.update_data(description="" if text in ("-", "—") else text)
    data = await state.get_data()
    names = list(
        await session.scalars(select(Level.name).where(Level.id.in_(data["level_ids"])).order_by(Level.position))
    )
    await state.set_state(NewTraining.confirm)
    await message.answer(
        "Проверьте:\n" + summarize_series(build_series_input(data), names),
        reply_markup=markup([("✅ Создать", "nf:ok"), ("✖️ Отмена", "nf:no")]),
    )


@router.callback_query(NewTraining.confirm, F.data == "nf:ok")
async def on_confirm(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    await ScheduleService(session).create_series(user, build_series_input(data), utcnow())
    await state.clear()
    log.info("training created by %s (one_off=%s)", user.telegram_id, data.get("one_off"))
    await cb.message.edit_text(
        "Готово ✅ Пост появится в чатах за 24 часа до старта. Расписание: /schedule",
        reply_markup=markup([("⬅️ Меню", "tr:menu")]),
    )
    await cb.answer()


@router.callback_query(F.data == "nf:no")
async def on_cancel_form(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cb.message.edit_text("Отменено", reply_markup=markup([("⬅️ Меню", "tr:menu")]))
    await cb.answer()


@router.callback_query(F.data.startswith("nf:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer("Форма устарела. Начните заново: /trainer", show_alert=True)
