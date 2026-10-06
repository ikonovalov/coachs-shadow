import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from bot.core.settings import set_setting
from bot.inventory.matching import MatchStatus, Wanted
from bot.inventory.models import InventoryOrder, OrderStatus, ensure_cleat_compat
from bot.inventory.service import InventoryService, NotGoing, busy_item_ids
from bot.participation.service import ParticipationService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_item, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=20)


async def world(session):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS, published_at=NOW - timedelta(hours=1)
    )
    await ensure_cleat_compat(session)
    return trainer, level, t


async def rider(session, uid, t, going=True, **profile):
    user = await add_user(session, uid)
    for key, value in profile.items():
        setattr(user, key, value)
    await session.commit()
    if going:
        await ParticipationService(session).join(t.id, uid, NOW)
    return user


BIKE = Wanted(frozenset({"bike"}))


# ---------- парк (админ) ----------

async def test_only_admin_manages_the_park_and_params_are_validated(session):
    admin = await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    svc = InventoryService(session)
    with pytest.raises(PermissionDenied):
        await svc.add_item(trainer, "helmet", "Шлем №1", helmet_size="M")
    item = await svc.add_item(admin, "helmet", "Шлем №1", helmet_size="M")
    assert item.active is True
    with pytest.raises(InvalidAction):
        await svc.add_item(admin, "kayak", "Лодка")
    with pytest.raises(InvalidAction):
        await svc.add_item(admin, "helmet", "Шлем №2", helmet_size="XXL")
    await svc.set_active(admin, item.id, False)
    assert item.active is False
    assert [i.id for i in await svc.list_items(include_inactive=False)] == []
    assert [i.id for i in await svc.list_items()] == [item.id]


# ---------- проверки перед заказом ----------

async def test_must_be_going(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t, going=False, height_cm=170)
    with pytest.raises(NotGoing):
        await InventoryService(session).suggest(t.id, user, BIKE, NOW)


async def test_not_for_cancelled_or_started_trainings(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t, height_cm=170)
    svc = InventoryService(session)
    with pytest.raises(InvalidAction):
        await svc.check_can_order(t.id, user.telegram_id, STARTS + timedelta(minutes=1))
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    with pytest.raises(InvalidAction):
        await svc.check_can_order(t.id, user.telegram_id, NOW)
    with pytest.raises(NotFound):
        await svc.check_can_order(9999, 1, NOW)


async def test_deadline_setting_closes_ordering(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t, height_cm=170)
    await set_setting(session, "inventory_deadline_h", "3")
    svc = InventoryService(session)
    await svc.check_can_order(t.id, user.telegram_id, STARTS - timedelta(hours=4))
    with pytest.raises(InvalidAction, match="3"):
        await svc.check_can_order(t.id, user.telegram_id, STARTS - timedelta(hours=2))


async def test_empty_deadline_means_no_limit(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t)
    await InventoryService(session).check_can_order(t.id, user.telegram_id, STARTS - timedelta(minutes=1))


async def test_suggest_needs_profile_pedal_choice_and_no_existing_order(session):
    _, _, t = await world(session)
    user = await rider(session, 1, t)  # профиль пуст
    svc = InventoryService(session)
    with pytest.raises(InvalidAction, match="профиля"):
        await svc.suggest(t.id, user, BIKE, NOW)
    with pytest.raises(InvalidAction, match="педал"):
        await svc.suggest(t.id, user, Wanted(frozenset({"pedals"})), NOW)
    user.height_cm = 170
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    await svc.confirm(t.id, 1, [bike.id], NOW)
    with pytest.raises(InvalidAction, match="уже есть"):
        await svc.suggest(t.id, user, BIKE, NOW)


# ---------- свободные единицы и пересечения ----------

async def test_suggest_matches_profile_and_skips_busy_items(session):
    _, level, t = await world(session)
    first = await rider(session, 1, t, height_cm=170)
    second = await rider(session, 2, t, height_cm=170)
    b1 = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    b2 = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)
    svc = InventoryService(session)
    (s,) = await svc.suggest(t.id, first, BIKE, NOW)
    assert (s.status, s.item_id) == (MatchStatus.EXACT, b1.id)
    await svc.confirm(t.id, 1, [b1.id], NOW)
    (s,) = await svc.suggest(t.id, second, BIKE, NOW)
    assert s.item_id == b2.id  # первый занят


async def test_overlapping_training_blocks_item_but_adjacent_one_does_not(session):
    trainer, level, a = await world(session)
    item = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    other = await add_user(session, 5)
    overlapping = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS + timedelta(minutes=30),
        published_at=NOW,
    )
    adjacent = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS + timedelta(minutes=90),  # ровно конец A
        published_at=NOW,
    )
    session.add(InventoryOrder(training_id=overlapping.id, user_id=5, item_id=item.id, status="active", created_at=NOW))
    await session.commit()
    assert await busy_item_ids(session, a) == {item.id}
    overlapping_order = await session.scalar(select(InventoryOrder))
    overlapping_order.status = OrderStatus.CANCELLED  # отмена освобождает сразу
    session.add(InventoryOrder(training_id=adjacent.id, user_id=5, item_id=item.id, status="active", created_at=NOW))
    await session.commit()
    assert await busy_item_ids(session, a) == set()  # «встык» не пересекается


# ---------- подтверждение ----------

async def test_confirm_creates_orders_with_price_and_blocks_the_same_kind_twice(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180, price_per_use=30000)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    result = await svc.confirm(t.id, 1, [bike.id, helmet.id], NOW)
    assert result.conflicts == [] and {o.item_id for o in result.orders} == {bike.id, helmet.id}
    assert next(o for o in result.orders if o.item_id == bike.id).price == 30000
    other_bike = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)
    with pytest.raises(InvalidAction, match="уже есть"):
        await svc.confirm(t.id, 1, [other_bike.id], NOW)


async def test_last_item_race_second_rider_gets_a_conflict_not_an_order(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    await rider(session, 2, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    await svc.confirm(t.id, 1, [bike.id], NOW)
    result = await svc.confirm(t.id, 2, [bike.id, helmet.id], NOW)
    assert result.conflicts == ["bike"]
    assert [o.item_id for o in result.orders] == [helmet.id]  # свободное сохранилось


async def test_inactive_item_is_a_conflict(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    bike.active = False
    await session.commit()
    result = await InventoryService(session).confirm(t.id, 1, [bike.id], NOW)
    assert result.conflicts == ["bike"] and result.orders == []


async def test_confirm_rejects_incompatible_pedals_and_shoes_and_duplicates(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    pedals = await add_item(session, "pedals", "Педали №1", pedal_type="look")
    shoes = await add_item(session, "shoes", "Туфли №1", shoe_eu=42, cleat_type="spd")
    other = await add_item(session, "pedals", "Педали №2", pedal_type="spd")
    svc = InventoryService(session)
    with pytest.raises(InvalidAction, match="не подходят"):
        await svc.confirm(t.id, 1, [pedals.id, shoes.id], NOW)
    with pytest.raises(InvalidAction):
        await svc.confirm(t.id, 1, [pedals.id, other.id], NOW)  # два вида «педали»
    with pytest.raises(InvalidAction):
        await svc.confirm(t.id, 1, [], NOW)
    with pytest.raises(NotFound):
        await svc.confirm(t.id, 1, [9999], NOW)
    assert list(await session.scalars(select(InventoryOrder))) == []


async def test_own_shoes_problem_uses_compat_data(session):
    await ensure_cleat_compat(session)
    svc = InventoryService(session)
    assert await svc.own_shoes_problem("spd", "spd") is None
    assert "не подходят" in await svc.own_shoes_problem("spd_sl", "spd")


# ---------- отмена ----------

async def test_cancel_order_rules_and_instant_release(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    await rider(session, 2, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    svc = InventoryService(session)
    (order,) = (await svc.confirm(t.id, 1, [bike.id], NOW)).orders
    with pytest.raises(PermissionDenied):
        await svc.cancel_order(order.id, 2, NOW)
    with pytest.raises(InvalidAction):
        await svc.cancel_order(order.id, 1, STARTS)  # уже началась
    await svc.cancel_order(order.id, 1, NOW)
    assert order.status == OrderStatus.CANCELLED
    result = await svc.confirm(t.id, 2, [bike.id], NOW)  # единица свободна сразу
    assert result.conflicts == []


async def test_cancel_for_participant_and_for_training(session):
    trainer, level, t = await world(session)
    await rider(session, 1, t)
    await rider(session, 2, t)
    b1 = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    b2 = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)
    h = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    await svc.confirm(t.id, 1, [b1.id, h.id], NOW)
    await svc.confirm(t.id, 2, [b2.id], NOW)
    cancelled = await svc.cancel_for_participant(t.id, 1)
    assert {o.item_id for o in cancelled} == {b1.id, h.id}
    assert await svc.cancel_for_participant(t.id, 1) == []  # повторно ничего
    rest = await svc.cancel_for_training(t.id)
    assert [o.item_id for o in rest] == [b2.id]
    assert list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == "active"))) == []


async def test_my_orders_lists_active_future_only(session):
    _, _, t = await world(session)
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmet = await add_item(session, "helmet", "Шлем №1", helmet_size="M")
    svc = InventoryService(session)
    orders = (await svc.confirm(t.id, 1, [bike.id, helmet.id], NOW)).orders
    await svc.cancel_order(next(o.id for o in orders if o.item_id == helmet.id), 1, NOW)
    views = await svc.my_orders(1, NOW)
    assert [v.item.id for v in views] == [bike.id] and views[0].training.id == t.id
    assert await svc.my_orders(1, STARTS + timedelta(hours=3)) == []


# ---------- сводка ----------

async def test_training_summary_is_for_those_who_manage_the_training(session):
    trainer, _, t = await world(session)
    admin = await add_user(session, 99, "admin")
    stranger = await add_user(session, 11, "trainer")
    await rider(session, 1, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    svc = InventoryService(session)
    await svc.confirm(t.id, 1, [bike.id], NOW)
    lines = await svc.training_summary(trainer, t.id)
    assert [(l.kind, l.frame, l.user_name) for l in lines] == [("bike", "M", "User 1")]
    assert len(await svc.training_summary(admin, t.id)) == 1
    with pytest.raises(PermissionDenied):
        await svc.training_summary(stranger, t.id)


# ---------- конкурентность (параллельные сессии) ----------

async def test_concurrent_confirms_never_give_the_last_item_twice(session, session_factory):
    _, _, t = await world(session)
    for uid in (1, 2, 3, 4):
        await rider(session, uid, t)
    bike = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    helmets = [await add_item(session, "helmet", f"Шлем №{n}", helmet_size="M") for n in (1, 2, 3, 4)]

    async def confirm(uid, helmet):
        async with session_factory() as s:
            return await InventoryService(s).confirm(t.id, uid, [bike.id, helmet.id], NOW)

    results = await asyncio.gather(*(confirm(uid, h) for uid, h in zip((1, 2, 3, 4), helmets)))
    winners = [r for r in results if r.conflicts == []]
    losers = [r for r in results if r.conflicts == ["bike"]]
    assert len(winners) == 1 and len(losers) == 3
    for result, helmet in zip(results, helmets):  # свободная позиция сохраняется у всех
        assert helmet.id in {o.item_id for o in result.orders}
    active = list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == "active")))
    assert [o.item_id for o in active].count(bike.id) == 1
    assert len(active) == 5  # 1 велосипед + 4 шлема


async def test_double_tap_confirm_orders_one_item_per_kind(session, session_factory):
    _, _, t = await world(session)
    await rider(session, 1, t)
    b1 = await add_item(session, "bike", "Велосипед №1", frame="M", height_min=160, height_max=180)
    b2 = await add_item(session, "bike", "Велосипед №2", frame="M", height_min=160, height_max=180)

    async def confirm(item):
        async with session_factory() as s:
            try:
                return await InventoryService(s).confirm(t.id, 1, [item.id], NOW)
            except InvalidAction:
                return None

    await asyncio.gather(confirm(b1), confirm(b2))
    active = list(await session.scalars(select(InventoryOrder).where(InventoryOrder.status == "active")))
    assert len(active) == 1  # один активный заказ на вид
