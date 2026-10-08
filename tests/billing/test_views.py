from datetime import timedelta

from bot.billing.models import Attendance, Charge, ChargeStatus
from bot.billing.service import BillingService
from bot.billing.views import BillingViews
from tests.billing.helpers import STARTS, done_training, ensure_user, give_subscription, sibling_training
from tests.factories import NOW


async def charged(session, **kw):
    t = await done_training(session, **kw)
    await BillingService(session).charge_training(t.id, NOW)
    return t


async def test_debtors_sum_pending_charges_and_unpaid_subscriptions_sorted_by_name(session):
    t = await charged(session, going=[1, 2, 3])
    (await ensure_user(session, 2)).name = "Борис"
    (await ensure_user(session, 1)).name = "Яна"
    await give_subscription(session, 1, [t.series_id], paid=False, price=300000)
    (await ensure_user(session, 3)).name = "Аня"
    charge3 = (await session.get(Charge, 3))  # id 3 — начисление пользователя 3
    charge3.status = ChargeStatus.PAID
    await session.commit()
    debtors = await BillingViews(session).debtors()
    assert [(d.user.name, d.total) for d in debtors] == [("Борис", 70000), ("Яна", 370000)]


async def test_user_debts_lists_charges_with_trainings(session):
    t = await charged(session, going=[1])
    await give_subscription(session, 1, [t.series_id], paid=False, price=300000, left=0)
    view = await BillingViews(session).user_debts(1)
    assert [(c.amount, tr.id) for c, tr in view.charges] == [(70000, t.id)]
    assert [s.price for s in view.subscriptions] == [300000]
    assert view.total == 370000


async def test_my_overview_shows_active_subscriptions_debts_and_history(session):
    t = await charged(session, going=[1])
    live = await give_subscription(session, 1, [t.series_id], name="живой")
    await give_subscription(session, 1, [t.series_id], name="пуст", left=0)
    await give_subscription(session, 1, [t.series_id], name="старый", expires_at=NOW - timedelta(days=1))
    old = await sibling_training(session, t, STARTS - timedelta(days=7), going=[1])
    await BillingService(session).charge_training(old.id, NOW)
    view = await BillingViews(session).my_overview(1, NOW)
    assert [s.name for s in view.subscriptions] == ["живой"]
    assert view.debts.total == 70000  # первая тренировка осталась в долге; вторая закрыта живым абонементом
    assert [c.status for c, _ in view.history] == [ChargeStatus.COVERED]
    assert view.subscriptions[0].id == live.id


async def test_refunds_due_lists_only_unreturned(session):
    t = await charged(session, going=[1, 2])
    charges = [await session.get(Charge, 1), await session.get(Charge, 2)]
    for charge in charges:
        charge.status, charge.refund_due = ChargeStatus.VOID, True
    charges[1].refunded_at = NOW
    await session.commit()
    rows = await BillingViews(session).refunds_due()
    assert [(c.id, u.telegram_id, tr.id) for c, u, tr in rows] == [(1, 1, t.id)]


async def test_roster_shows_going_and_marked_users(session):
    t = await charged(session, going=[1, 2])
    await ensure_user(session, 3)
    session.add_all([
        Attendance(training_id=t.id, user_id=2, present=False, marked_by=10, marked_at=NOW),
        Attendance(training_id=t.id, user_id=3, present=True, marked_by=10, marked_at=NOW),
    ])
    await session.commit()
    roster = await BillingViews(session).roster(t.id)
    assert [(u.telegram_id, present) for u, present in roster] == [(1, True), (2, False), (3, True)]


async def test_payment_hint_for_subscription_price_and_surcharge(session):
    t = await done_training(session, price=70000, surcharge=50000, going=[1, 2])
    t.status = "scheduled"
    t.capacity = 4
    await session.commit()
    views = BillingViews(session)
    hint = await views.payment_hint(t.id, 3, NOW)
    assert "К оплате: 700 ₽" in hint and "Доплата ≈ 625–750 ₽" in hint  # 2 записаны: 750; полная группа (4): 625
    await give_subscription(session, 3, [t.series_id], name="Утро", left=2)
    hint = await views.payment_hint(t.id, 3, NOW)
    assert "Будет списано с абонемента «Утро» (осталось 2)" in hint and "К оплате" not in hint


async def test_payment_hint_is_none_for_free_training(session):
    t = await done_training(session, price=None, going=[1])
    t.status = "scheduled"
    await session.commit()
    assert await BillingViews(session).payment_hint(t.id, 2, NOW) is None
    assert await BillingViews(session).payment_hint(9999, 2, NOW) is None
