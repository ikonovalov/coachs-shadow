import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.billing.catalog import CatalogService
from bot.billing.forms import PLAN_FORMAT, PRICE_FORMAT, parse_plan_line, parse_prices_line
from bot.billing.models import SubscriptionPlan
from bot.billing.render import charge_label
from bot.billing.service import BillingService
from bot.billing.views import BillingViews
from bot.core.db import utcnow
from bot.core.errors import DomainError, NotFound
from bot.core.messenger import Messenger
from bot.core.money import format_money
from bot.core.permissions import require_admin
from bot.publishing.render import KIND_NAMES
from bot.schedule.forms import series_title
from bot.schedule.models import TrainingKind, TrainingSeries
from bot.schedule.service import ScheduleService, series_to_input
from bot.telegram.handlers.picker import on_pick, start_picker
from bot.telegram.menu import PAY_MENU
from bot.telegram.states import PlanForm, PriceInput
from bot.telegram.ui import club_tz, markup, restore_form, safe_edit, take_form
from bot.usecases import issue_subscription, mark_charge_paid, mark_subscription_paid, mark_user_paid
from bot.users.models import User

log = logging.getLogger(__name__)

router = Router()
router.callback_query.filter(F.message.chat.type == "private")
router.message.filter(F.chat.type == "private")

BACK_TO_PAY = [("⬅️ Меню", "pay:menu")]


def _id(cb: CallbackQuery, index: int = 2) -> int:
    return int(cb.data.split(":")[index])


def _money(value: int | None) -> str:
    return "—" if value is None else format_money(value)


@router.callback_query(F.data == "pay:menu")
async def on_menu(cb: CallbackQuery, user: User, state: FSMContext) -> None:
    require_admin(user)
    await state.clear()
    await safe_edit(cb.message, "💰 Оплаты", reply_markup=PAY_MENU)
    await cb.answer()


# ---------- должники ----------

async def _show_debtors(cb: CallbackQuery, session: AsyncSession) -> None:
    debtors = await BillingViews(session).debtors()
    rows = [[(f"👤 {d.user.name} · {format_money(d.total)}", f"pay:du:{d.user.telegram_id}")] for d in debtors]
    rows.append(BACK_TO_PAY)
    await safe_edit(cb.message, "Должники:" if debtors else "Долгов нет 🎉", reply_markup=markup(*rows))


@router.callback_query(F.data == "pay:debt")
async def on_debtors(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_debtors(cb, session)
    await cb.answer()


async def _show_debtor(cb: CallbackQuery, session: AsyncSession, user_id: int) -> None:
    target = await session.get(User, user_id)
    if target is None:
        raise NotFound("Пользователь не найден")
    tz = await club_tz(session)
    view = await BillingViews(session).user_debts(user_id)
    rows = [
        [(f"✅ {charge_label(c, t, tz)} · {format_money(c.amount)}", f"pay:cp:{c.id}")] for c, t in view.charges
    ]
    rows += [[(f"✅ Абонемент «{s.name}» · {format_money(s.price)}", f"pay:sp:{s.id}")] for s in view.subscriptions]
    if view.charges or view.subscriptions:
        rows.append([("✅ Всё оплачено", f"pay:ua:{user_id}")])
    rows.append([("⬅️ К должникам", "pay:debt")])
    text = f"{target.name}: к оплате {format_money(view.total)}" if view.total > 0 else f"{target.name}: долгов нет"
    await safe_edit(cb.message, text, reply_markup=markup(*rows))


@router.callback_query(F.data.regexp(r"^pay:du:\d+$"))
async def on_debtor(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_debtor(cb, session, _id(cb))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:cp:\d+$"))
async def on_charge_paid(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    charge = await mark_charge_paid(session, messenger, user, _id(cb), utcnow())
    await _show_debtor(cb, session, charge.user_id)
    await cb.answer("Оплачено")


@router.callback_query(F.data.regexp(r"^pay:sp:\d+$"))
async def on_subscription_paid(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    sub = await mark_subscription_paid(session, messenger, user, _id(cb), utcnow())
    await _show_debtor(cb, session, sub.user_id)
    await cb.answer("Оплачено")


@router.callback_query(F.data.regexp(r"^pay:ua:\d+$"))
async def on_user_paid(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    user_id = _id(cb)
    await mark_user_paid(session, messenger, user, user_id, utcnow())
    await _show_debtor(cb, session, user_id)
    await cb.answer("Оплачено")


# ---------- выдача абонемента ----------

@router.callback_query(F.data == "pay:iss")
async def on_issue(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await start_picker(cb, state, purpose="issue_sub", ctx={}, prompt="Кому выдать абонемент?")


@on_pick("issue_sub")
async def picked_for_issue(
    cb: CallbackQuery, session: AsyncSession, user: User, messenger: Messenger, ctx: dict, picked_id: int
) -> None:
    require_admin(user)
    plans = await CatalogService(session).list_plans()
    if not plans:
        await cb.message.answer("Тарифов нет. Создайте тариф: 💰 Оплаты → 📑 Тарифы", reply_markup=markup(BACK_TO_PAY))
        await cb.answer()
        return
    rows = [
        [(f"{p.name} · {p.sessions} зан. · {format_money(p.price)}", f"pay:ip:{picked_id}:{p.id}")] for p in plans
    ]
    rows.append(BACK_TO_PAY)
    await cb.message.answer("Выберите тариф:", reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:ip:\d+:\d+$"))
async def on_issue_plan(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    user_id, plan_id = _id(cb), _id(cb, 3)
    plan = await session.get(SubscriptionPlan, plan_id)
    target = await session.get(User, user_id)
    if plan is None or target is None:
        raise NotFound("Тариф или пользователь не найден")
    text = f"Выдать «{plan.name}» ({plan.sessions} занятий, {format_money(plan.price)}) — {target.name}?"
    rows = [
        [("✅ Выдать, оплачен", f"pay:ig:{user_id}:{plan_id}:1"), ("✅ Выдать, не оплачен", f"pay:ig:{user_id}:{plan_id}:0")],
        BACK_TO_PAY,
    ]
    await safe_edit(cb.message, text, reply_markup=markup(*rows))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:ig:\d+:\d+:[01]$"))
async def on_issue_do(cb: CallbackQuery, session: AsyncSession, messenger: Messenger, user: User) -> None:
    parts = cb.data.split(":")
    sub = await issue_subscription(session, messenger, user, int(parts[2]), int(parts[3]), parts[4] == "1", utcnow())
    await safe_edit(cb.message, f"Абонемент «{sub.name}» выдан", reply_markup=markup(BACK_TO_PAY))
    await cb.answer("Выдан")


# ---------- тарифы ----------

async def _show_plans(cb: CallbackQuery, session: AsyncSession) -> None:
    plans = await CatalogService(session).list_plans()
    rows = [[(f"❌ {p.name} · {p.sessions} зан. · {format_money(p.price)}", f"pay:pd:{p.id}")] for p in plans]
    rows += [[("➕ Тариф", "pay:pa")], BACK_TO_PAY]
    await safe_edit(
        cb.message, "Тарифы (нажмите, чтобы удалить):" if plans else "Тарифов пока нет", reply_markup=markup(*rows)
    )


@router.callback_query(F.data == "pay:pl")
async def on_plans(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_plans(cb, session)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:pd:\d+$"))
async def on_plan_delete_ask(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    plan_id = _id(cb)
    plans = {p.id: p for p in await CatalogService(session).list_plans()}
    if plan_id not in plans:
        raise NotFound("Тариф не найден")
    await safe_edit(
        cb.message,
        f"Удалить тариф «{plans[plan_id].name}»? Уже выданные абонементы не изменятся.",
        reply_markup=markup([("✅ Удалить", f"pay:pdy:{plan_id}"), ("✖️ Отмена", "pay:pl")]),
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:pdy:\d+$"))
async def on_plan_delete_do(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await CatalogService(session).delete_plan(user, _id(cb))
    await _show_plans(cb, session)
    await cb.answer("Тариф удалён")


@router.callback_query(F.data == "pay:pa")
async def on_plan_add(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    require_admin(user)
    await state.clear()
    await state.set_state(PlanForm.line)
    await cb.message.answer(f"Новый тариф.\n{PLAN_FORMAT}", reply_markup=markup(BACK_TO_PAY))
    await cb.answer()


async def _plan_series_screen(session: AsyncSession, user: User, picked: list[int]) -> tuple[str, object]:
    series = await ScheduleService(session).active_series(user, utcnow())
    rows = [
        [(("☑️ " if s.id in picked else "⬜ ") + series_title(series_to_input(s)), f"pay:pst:{s.id}")] for s in series
    ]
    rows += [[("✅ Создать тариф", "pay:pok")], [("✖️ Отмена", "pay:menu")]]
    text = "Выберите серии, на которые действует абонемент:" if series else "Активных серий нет — создайте серию тренером."
    return text, markup(*rows)


@router.message(PlanForm.line, F.text, ~F.text.startswith("/"))
async def on_plan_line(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    draft = parse_plan_line(message.text)  # неверный ввод: подсказка, состояние сохраняется
    await state.set_state(PlanForm.series)
    await state.update_data(
        draft={"name": draft.name, "sessions": draft.sessions, "price": draft.price, "valid_months": draft.valid_months},
        picked=[],
    )
    text, keyboard = await _plan_series_screen(session, user, [])
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(PlanForm.series, F.data.regexp(r"^pay:pst:\d+$"))
async def on_plan_toggle(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    series_id = _id(cb)
    picked = list((await state.get_data()).get("picked", []))
    picked = [i for i in picked if i != series_id] if series_id in picked else picked + [series_id]
    await state.update_data(picked=picked)
    text, keyboard = await _plan_series_screen(session, user, picked)
    await safe_edit(cb.message, text, reply_markup=keyboard)
    await cb.answer()


@router.callback_query(PlanForm.series, F.data == "pay:pok")
async def on_plan_create(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await take_form(state, PlanForm.series, key="draft")
    if data is None:
        await cb.answer()
        return
    draft = data["draft"]
    try:
        plan = await CatalogService(session).create_plan(
            user, draft["name"], draft["sessions"], draft["price"], draft["valid_months"], data.get("picked", [])
        )
    except DomainError:
        await restore_form(state, PlanForm.series, data)
        raise
    log.info("plan %s created by %s", plan.id, user.telegram_id)
    await safe_edit(cb.message, f"Тариф «{plan.name}» создан", reply_markup=markup(BACK_TO_PAY))
    await cb.answer("Создан")


# ---------- цены ----------

@router.callback_query(F.data == "pay:pr")
async def on_prices(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    catalog = CatalogService(session)
    rows = []
    for kind in TrainingKind.ALL:
        prices = await catalog.type_prices(kind)
        rows.append([(f"{KIND_NAMES[kind]}: {_money(prices.single_price)} / {_money(prices.surcharge_per_person)}", f"pay:pt:{kind}")])
    rows += [[("🔁 Цены серий", "pay:ps")], BACK_TO_PAY]
    await safe_edit(
        cb.message, "Цены по типу тренировки (разовая / доплата). Нажмите, чтобы изменить:", reply_markup=markup(*rows)
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:pt:[a-z]+$"))
async def on_price_type(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    kind = cb.data.split(":")[2]
    if kind not in TrainingKind.ALL:
        raise NotFound("Неизвестный тип тренировки")
    prices = await CatalogService(session).type_prices(kind)
    await state.clear()
    await state.set_state(PriceInput.value)
    await state.update_data(target="type", key=kind)
    await cb.message.answer(
        f"{KIND_NAMES[kind]}: сейчас {_money(prices.single_price)} / {_money(prices.surcharge_per_person)}.\n{PRICE_FORMAT}",
        reply_markup=markup(BACK_TO_PAY),
    )
    await cb.answer()


@router.callback_query(F.data == "pay:ps")
async def on_series_prices(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    series = await ScheduleService(session).active_series(user, utcnow())
    rows = [[(series_title(series_to_input(s)), f"pay:psi:{s.id}")] for s in series]
    rows.append([("⬅️ Цены", "pay:pr")])
    await safe_edit(
        cb.message, "Серии (нажмите, чтобы задать свои цены):" if series else "Активных серий нет",
        reply_markup=markup(*rows),
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:psi:\d+$"))
async def on_price_series(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    series = await session.get(TrainingSeries, _id(cb))
    if series is None:
        raise NotFound("Серия не найдена")
    await state.clear()
    await state.set_state(PriceInput.value)
    await state.update_data(target="series", key=series.id)
    await cb.message.answer(
        f"{series_title(series_to_input(series))}: сейчас {_money(series.single_price)} / "
        f"{_money(series.surcharge_per_person)} (— значит «как у типа»).\n{PRICE_FORMAT}",
        reply_markup=markup(BACK_TO_PAY),
    )
    await cb.answer()


@router.message(PriceInput.value, F.text, ~F.text.startswith("/"))
async def on_price_value(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    require_admin(user)
    prices = parse_prices_line(message.text)  # неверный ввод: подсказка, состояние сохраняется
    data = await state.get_data()
    catalog = CatalogService(session)
    if data["target"] == "type":
        await catalog.set_type_prices(user, data["key"], prices)
    else:
        await catalog.set_series_prices(user, int(data["key"]), prices)
    await state.clear()
    await message.answer("Цены сохранены. Новые тренировки возьмут их при создании.", reply_markup=markup(BACK_TO_PAY))


# ---------- возвраты ----------

async def _show_refunds(cb: CallbackQuery, session: AsyncSession) -> None:
    tz = await club_tz(session)
    rows = [
        [(f"↩️ {u.name} · {format_money(c.amount)} · {charge_label(c, t, tz)}", f"pay:rfd:{c.id}")]
        for c, u, t in await BillingViews(session).refunds_due()
    ]
    text = "К возврату (нажмите после возврата денег):" if rows else "Возвратов нет"
    rows.append(BACK_TO_PAY)
    await safe_edit(cb.message, text, reply_markup=markup(*rows))


@router.callback_query(F.data == "pay:rf")
async def on_refunds(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await _show_refunds(cb, session)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:rfd:\d+$"))
async def on_refund_ask(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    require_admin(user)
    await safe_edit(
        cb.message, "Деньги ученику возвращены?",
        reply_markup=markup([("✅ Возвращено", f"pay:rfy:{_id(cb)}"), ("✖️ Отмена", "pay:rf")]),
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pay:rfy:\d+$"))
async def on_refund_do(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    await BillingService(session).mark_refunded(user, _id(cb), utcnow())
    await _show_refunds(cb, session)
    await cb.answer("Отмечено")


@router.callback_query(F.data.startswith("pay:"))
async def on_stale(cb: CallbackQuery) -> None:
    await cb.answer("Кнопка устарела. Откройте раздел заново: /admin", show_alert=True)
