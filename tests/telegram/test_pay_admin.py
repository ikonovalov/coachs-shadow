import pytest
from sqlalchemy import select

from bot.billing.catalog import CatalogService
from bot.billing.models import Charge, ChargeStatus, Subscription
from bot.billing.queries import Prices
from bot.billing.service import BillingService
from bot.core.errors import PermissionDenied
from bot.schedule.models import TrainingSeries
from bot.telegram.handlers import pay_admin
from bot.telegram.states import PlanForm, PriceInput
from tests.billing.helpers import done_training, ensure_user
from tests.factories import NOW
from tests.telegram.fakes import FakeCallback, FakeMessage, callback_data, fsm


async def admin(session):
    return await ensure_user(session, 99, "admin")


async def recurring(session, training):
    """Список активных серий показывает только повторяющиеся: делаем серию тренировки вторничной."""
    series = await session.get(TrainingSeries, training.series_id)
    series.weekdays = "2"
    await session.commit()


async def test_every_screen_is_admin_only(session):
    member = await ensure_user(session, 1)
    for handler, data in [
        (pay_admin.on_menu, "pay:menu"), (pay_admin.on_debtors, "pay:debt"), (pay_admin.on_refunds, "pay:rf"),
        (pay_admin.on_plans, "pay:pl"), (pay_admin.on_prices, "pay:pr"),
    ]:
        args = [FakeCallback(data), session, member] if handler is not pay_admin.on_menu else [FakeCallback(data), member, fsm()]
        with pytest.raises(PermissionDenied):
            await handler(*args)


async def test_debtors_list_and_marking_everything_paid(session, messenger):
    t = await done_training(session, going=[1, 2])
    await BillingService(session).charge_training(t.id, NOW)
    me = await admin(session)
    cb = FakeCallback("pay:debt")
    await pay_admin.on_debtors(cb, session, me)
    text, markup = cb.message.edits[-1]
    assert text == "Должники:"
    assert callback_data(markup) == ["pay:du:1", "pay:du:2", "pay:menu"]

    cb = FakeCallback("pay:du:1")
    await pay_admin.on_debtor(cb, session, me)
    assert callback_data(cb.message.edits[-1][1]) == ["pay:cp:1", "pay:ua:1", "pay:debt"]

    cb = FakeCallback("pay:ua:1")
    await pay_admin.on_user_paid(cb, session, messenger, me)
    assert (await session.get(Charge, 1)).status == ChargeStatus.PAID
    assert (1, "✅ Оплата принята. Спасибо!") in messenger.notifications
    assert "долгов нет" in cb.message.edits[-1][0]


async def test_marking_one_charge_paid_twice_does_not_crash_the_screen(session, messenger):
    t = await done_training(session, going=[1])
    await BillingService(session).charge_training(t.id, NOW)
    me = await admin(session)
    await pay_admin.on_charge_paid(FakeCallback("pay:cp:1"), session, messenger, me)
    with pytest.raises(Exception) as exc:  # DomainError обрабатывает общий errors-хендлер
        await pay_admin.on_charge_paid(FakeCallback("pay:cp:1"), session, messenger, me)
    assert "уже оплачено" in str(exc.value)


async def test_plan_creation_flow(session, messenger):
    t = await done_training(session)
    await recurring(session, t)
    me = await admin(session)
    state = fsm()
    await pay_admin.on_plan_add(FakeCallback("pay:pa"), state, me)
    assert await state.get_state() == PlanForm.line.state

    msg = FakeMessage(text="Утро+вечер; 8; 5500; 2")
    await pay_admin.on_plan_line(msg, state, session, me)
    assert await state.get_state() == PlanForm.series.state
    assert callback_data(msg.answers[-1][1])[0] == f"pay:pst:{t.series_id}"

    cb = FakeCallback(f"pay:pst:{t.series_id}")
    await pay_admin.on_plan_toggle(cb, state, session, me)
    assert "☑️" in cb.message.edits[-1][0] or "☑️" in cb.message.edits[-1][1].inline_keyboard[0][0].text

    cb = FakeCallback("pay:pok")
    await pay_admin.on_plan_create(cb, state, session, me)
    plans = await CatalogService(session).list_plans()
    assert [(p.name, p.sessions, p.price, p.valid_months) for p in plans] == [("Утро+вечер", 8, 550000, 2)]
    assert await state.get_state() is None


async def test_plan_creation_without_series_keeps_the_form(session):
    t = await done_training(session)
    me = await admin(session)
    state = fsm()
    await pay_admin.on_plan_add(FakeCallback("pay:pa"), state, me)
    await pay_admin.on_plan_line(FakeMessage(text="План; 4; 100"), state, session, me)
    with pytest.raises(Exception):
        await pay_admin.on_plan_create(FakeCallback("pay:pok"), state, session, me)
    assert await state.get_state() == PlanForm.series.state  # форму вернули


async def test_bad_plan_line_keeps_waiting_for_input(session):
    me = await admin(session)
    state = fsm()
    await pay_admin.on_plan_add(FakeCallback("pay:pa"), state, me)
    with pytest.raises(Exception):
        await pay_admin.on_plan_line(FakeMessage(text="мусор"), state, session, me)
    assert await state.get_state() == PlanForm.line.state


async def test_plan_deletion_follows_confirm_pattern(session):
    t = await done_training(session)
    me = await admin(session)
    plan = await CatalogService(session).create_plan(me, "Утро", 4, 100, 1, [t.series_id])
    cb = FakeCallback("pay:pl")
    await pay_admin.on_plans(cb, session, me)
    assert callback_data(cb.message.edits[-1][1])[0] == f"pay:pd:{plan.id}"
    cb = FakeCallback(f"pay:pd:{plan.id}")
    await pay_admin.on_plan_delete_ask(cb, session, me)
    assert f"pay:pdy:{plan.id}" in callback_data(cb.message.edits[-1][1])
    cb = FakeCallback(f"pay:pdy:{plan.id}")
    await pay_admin.on_plan_delete_do(cb, session, me)
    assert await CatalogService(session).list_plans() == []


async def test_type_price_input(session):
    me = await admin(session)
    state = fsm()
    await pay_admin.on_price_type(FakeCallback("pay:pt:track"), state, session, me)
    assert await state.get_state() == PriceInput.value.state
    msg = FakeMessage(text="900; 500")
    await pay_admin.on_price_value(msg, state, session, me)
    assert await CatalogService(session).type_prices("track") == Prices(90000, 50000)
    assert await state.get_state() is None


async def test_series_price_input(session):
    t = await done_training(session)
    me = await admin(session)
    state = fsm()
    await pay_admin.on_price_series(FakeCallback(f"pay:psi:{t.series_id}"), state, session, me)
    await pay_admin.on_price_value(FakeMessage(text="-; 0"), state, session, me)
    series = await session.get(TrainingSeries, t.series_id)
    assert (series.single_price, series.surcharge_per_person) == (None, 0)


async def test_refund_flow(session):
    t = await done_training(session, going=[1])
    await BillingService(session).charge_training(t.id, NOW)
    me = await admin(session)
    charge = await session.get(Charge, 1)
    charge.status, charge.refund_due = ChargeStatus.VOID, True
    await session.commit()
    cb = FakeCallback("pay:rf")
    await pay_admin.on_refunds(cb, session, me)
    assert callback_data(cb.message.edits[-1][1]) == ["pay:rfd:1", "pay:menu"]
    cb = FakeCallback("pay:rfd:1")
    await pay_admin.on_refund_ask(cb, session, me)
    assert "pay:rfy:1" in callback_data(cb.message.edits[-1][1])
    await pay_admin.on_refund_do(FakeCallback("pay:rfy:1"), session, me)
    assert (await session.get(Charge, 1)).refunded_at is not None


async def test_issue_subscription_flow(session, messenger):
    t = await done_training(session, going=[1])
    me = await admin(session)
    plan = await CatalogService(session).create_plan(me, "Утро", 4, 300000, 1, [t.series_id])
    cb = FakeCallback(f"pay:ip:1:{plan.id}")
    await pay_admin.on_issue_plan(cb, session, me)
    assert callback_data(cb.message.edits[-1][1]) == [f"pay:ig:1:{plan.id}:1", f"pay:ig:1:{plan.id}:0", "pay:menu"]
    await pay_admin.on_issue_do(FakeCallback(f"pay:ig:1:{plan.id}:0"), session, messenger, me)
    sub = (await session.scalars(select(Subscription))).one()
    assert sub.paid_at is None and sub.user_id == 1
    assert any("Утро" in text for uid, text in messenger.notifications if uid == 1)
