import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.billing.catalog import CatalogService
from bot.billing.models import Charge, ChargeKind, ChargeStatus, Subscription, SubscriptionSeries
from bot.billing.queries import Prices
from bot.billing.service import BillingService
from bot.core.errors import NotFound, PermissionDenied
from bot.inventory.models import InventoryOrder, OrderStatus
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import TrainingSeries, TrainingStatus
from tests.billing.helpers import STARTS, done_training, ensure_user, give_subscription, sibling_training
from tests.factories import NOW, add_item


async def charges(session, training_id, user_id=None):
    query = select(Charge).where(Charge.training_id == training_id).order_by(Charge.user_id, Charge.kind, Charge.id)
    if user_id is not None:
        query = query.where(Charge.user_id == user_id)
    return list(await session.scalars(query))


async def test_issue_subscription_copies_plan_and_sets_expiry(session):
    t = await done_training(session)
    admin = await ensure_user(session, 99, "admin")
    await ensure_user(session, 1)
    plan = await CatalogService(session).create_plan(admin, "Утро+вечер", 8, 550000, 1, [t.series_id])
    sub = await BillingService(session).issue_subscription(admin, 1, plan.id, paid=True, now=NOW)
    assert (sub.name, sub.sessions_total, sub.sessions_left, sub.price) == ("Утро+вечер", 8, 8, 550000)
    assert sub.expires_at is not None and sub.expires_at > NOW + timedelta(days=27)
    assert sub.paid_at == NOW and sub.paid_by == 99 and sub.issued_by == 99
    assert [r.series_id for r in await session.scalars(select(SubscriptionSeries))] == [t.series_id]
    unpaid = await BillingService(session).issue_subscription(admin, 1, plan.id, paid=False, now=NOW)
    assert unpaid.paid_at is None


async def test_issue_subscription_checks_rights_and_targets(session):
    t = await done_training(session)
    admin = await ensure_user(session, 99, "admin")
    member = await ensure_user(session, 1)
    plan = await CatalogService(session).create_plan(admin, "План", 4, 100, None, [t.series_id])
    svc = BillingService(session)
    with pytest.raises(PermissionDenied):
        await svc.issue_subscription(member, 1, plan.id, paid=True, now=NOW)
    with pytest.raises(NotFound):
        await svc.issue_subscription(admin, 777, plan.id, paid=True, now=NOW)
    with pytest.raises(NotFound):
        await svc.issue_subscription(admin, 1, 9999, paid=True, now=NOW)


async def test_close_charges_single_price_without_subscription(session):
    t = await done_training(session, going=[1, 2])
    created = await BillingService(session).charge_training(t.id, NOW)
    assert [(c.user_id, c.kind, c.status, c.amount) for c in created] == [
        (1, ChargeKind.SESSION, ChargeStatus.PENDING, 70000),
        (2, ChargeKind.SESSION, ChargeStatus.PENDING, 70000),
    ]


async def test_subscription_covers_the_session_and_loses_one_unit(session):
    t = await done_training(session, going=[1, 2])
    sub = await give_subscription(session, 1, [t.series_id], sessions=4)
    await BillingService(session).charge_training(t.id, NOW)
    first = (await charges(session, t.id, 1))[0]
    assert (first.status, first.subscription_id, first.amount) == (ChargeStatus.COVERED, sub.id, 70000)
    assert (await charges(session, t.id, 2))[0].status == ChargeStatus.PENDING
    assert (await session.get(Subscription, sub.id)).sessions_left == 3


async def test_unsuitable_subscriptions_are_not_used(session):
    t = await done_training(session, going=[1])
    foreign = TrainingSeries(
        trainer_id=10, place="Х", kind="road", weekdays="", start_date=STARTS.date(), start_time=STARTS.time(),
        duration_min=90,
    )
    session.add(foreign)
    await session.commit()
    expired = await give_subscription(session, 1, [t.series_id], expires_at=STARTS - timedelta(days=1), name="просрочен")
    empty = await give_subscription(session, 1, [t.series_id], left=0, name="пуст")
    wrong_series = await give_subscription(session, 1, [foreign.id], name="чужая серия")
    await BillingService(session).charge_training(t.id, NOW)
    assert (await charges(session, t.id, 1))[0].status == ChargeStatus.PENDING
    for sub in (expired, empty, wrong_series):
        assert (await session.get(Subscription, sub.id)).sessions_left == (0 if sub is empty else 4)


async def test_earlier_expiring_subscription_goes_first_and_unlimited_goes_last(session):
    t = await done_training(session, going=[1])
    unlimited = await give_subscription(session, 1, [t.series_id], name="бессрочный")
    soon = await give_subscription(session, 1, [t.series_id], expires_at=NOW + timedelta(days=3), name="скоро")
    later = await give_subscription(session, 1, [t.series_id], expires_at=NOW + timedelta(days=30), name="позже")
    await BillingService(session).charge_training(t.id, NOW)
    assert (await charges(session, t.id, 1))[0].subscription_id == soon.id
    assert (await session.get(Subscription, later.id)).sessions_left == 4
    assert (await session.get(Subscription, unlimited.id)).sessions_left == 4


async def test_subscription_issued_after_the_training_does_not_cover_it(session):
    t = await done_training(session, going=[1])
    sub = await give_subscription(session, 1, [t.series_id])
    sub.starts_at = STARTS + timedelta(minutes=5)
    await session.commit()
    await BillingService(session).charge_training(t.id, NOW)
    assert (await charges(session, t.id, 1))[0].status == ChargeStatus.PENDING


async def test_surcharge_is_split_between_attendees_with_the_trainers_share(session):
    t = await done_training(session, price=0, surcharge=50000, going=[1, 2, 3])
    await BillingService(session).charge_training(t.id, NOW)
    rows = await charges(session, t.id)
    assert [(c.kind, c.amount, c.status) for c in rows] == [(ChargeKind.SURCHARGE, 66667, ChargeStatus.PENDING)] * 3


async def test_surcharge_is_not_covered_by_a_subscription(session):
    t = await done_training(session, price=70000, surcharge=50000, going=[1])
    await give_subscription(session, 1, [t.series_id])
    await BillingService(session).charge_training(t.id, NOW)
    kinds = {c.kind: c.status for c in await charges(session, t.id, 1)}
    assert kinds == {ChargeKind.SESSION: ChargeStatus.COVERED, ChargeKind.SURCHARGE: ChargeStatus.PENDING}


async def test_second_run_changes_nothing(session):
    t = await done_training(session, surcharge=50000, going=[1, 2])
    sub = await give_subscription(session, 1, [t.series_id])
    svc = BillingService(session)
    assert len(await svc.charge_training(t.id, NOW)) == 4
    assert await svc.charge_training(t.id, NOW) == []
    assert len(await charges(session, t.id)) == 4
    assert (await session.get(Subscription, sub.id)).sessions_left == 3


async def test_scheduled_and_cancelled_trainings_are_not_charged(session):
    t = await done_training(session, going=[1])
    for status in (TrainingStatus.SCHEDULED, TrainingStatus.CANCELLED):
        t.status = status
        await session.commit()
        assert await BillingService(session).charge_training(t.id, NOW) == []
    assert await BillingService(session).charge_training(9999, NOW) == []


async def test_free_training_without_prices_creates_no_charges(session):
    t = await done_training(session, price=None, going=[1])
    assert await BillingService(session).charge_training(t.id, NOW) == []


async def test_missing_snapshot_falls_back_to_current_rules(session):
    t = await done_training(session, price=None, going=[1])
    admin = await ensure_user(session, 99, "admin")
    await CatalogService(session).set_type_prices(admin, "road", Prices(65000, None))
    await BillingService(session).charge_training(t.id, NOW)
    assert (await charges(session, t.id, 1))[0].amount == 65000


async def test_inventory_orders_become_charges(session):
    t = await done_training(session, price=None, going=[1])
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", price_per_use=30000)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")  # без цены — не начисляется
    session.add_all([
        InventoryOrder(training_id=t.id, user_id=1, item_id=bike.id, status=OrderStatus.ACTIVE, price=30000, created_at=NOW),
        InventoryOrder(training_id=t.id, user_id=1, item_id=helmet.id, status=OrderStatus.ACTIVE, price=None, created_at=NOW),
    ])
    await session.commit()
    await BillingService(session).charge_training(t.id, NOW)
    rows = await charges(session, t.id, 1)
    assert [(c.kind, c.amount, c.status) for c in rows] == [(ChargeKind.INVENTORY, 30000, ChargeStatus.PENDING)]
    assert rows[0].ref_id > 0


async def test_cancelled_inventory_order_is_not_charged(session):
    t = await done_training(session, price=None, going=[1])
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", price_per_use=30000)
    session.add(InventoryOrder(
        training_id=t.id, user_id=1, item_id=bike.id, status=OrderStatus.CANCELLED, price=30000, created_at=NOW,
    ))
    await session.commit()
    assert await BillingService(session).charge_training(t.id, NOW) == []


async def test_declined_users_are_not_charged(session):
    t = await done_training(session, going=[1])
    await ensure_user(session, 2)
    session.add(Participation(training_id=t.id, user_id=2, status=ParticipationStatus.DECLINED, updated_at=NOW))
    await session.commit()
    created = await BillingService(session).charge_training(t.id, NOW)
    assert [c.user_id for c in created] == [1]


async def test_last_unit_goes_to_exactly_one_of_two_concurrent_closes(session, session_factory):
    a = await done_training(session, going=[1])
    b = await sibling_training(session, a, STARTS - timedelta(days=1), going=[1])
    sub = await give_subscription(session, 1, [a.series_id], sessions=1)

    async def close(training_id):
        async with session_factory() as s:
            return await BillingService(s).charge_training(training_id, NOW)

    await asyncio.gather(close(a.id), close(b.id))
    statuses = sorted(c.status for c in await session.scalars(select(Charge)))
    assert statuses == [ChargeStatus.COVERED, ChargeStatus.PENDING]
    await session.refresh(sub)  # списания шли в других сессиях: identity map этой сессии устарела
    assert sub.sessions_left == 0


async def test_parallel_closes_with_several_participants_do_not_crash_or_double_charge(session, session_factory):
    t = await done_training(session, going=[1, 2, 3, 4])
    subs = [await give_subscription(session, uid, [t.series_id], sessions=5) for uid in (1, 2, 3, 4)]

    async def close():
        async with session_factory() as s:
            return await BillingService(s).charge_training(t.id, NOW)

    results = await asyncio.gather(*(close() for _ in range(3)), return_exceptions=True)
    assert [r for r in results if isinstance(r, Exception)] == []
    assert len(list(await session.scalars(select(Charge)))) == 4
    for sub in subs:
        await session.refresh(sub)
        assert sub.sessions_left == 4  # каждому списано ровно одно занятие
