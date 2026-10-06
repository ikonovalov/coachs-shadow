import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.inventory.models import CleatCompat, InventoryOrder, ensure_cleat_compat
from tests.factories import NOW, add_item, add_level, add_training, add_user
from datetime import timedelta


async def test_item_roundtrip_and_defaults(session):
    item = await add_item(session, "bike", "Велосипед №7", frame="M", height_min=165, height_max=178)
    item_id, model = item.id, type(item)
    session.expire_all()
    fresh = await session.get(model, item_id)
    assert (fresh.kind, fresh.active, fresh.frame, fresh.price_per_use) == ("bike", True, "M", None)


async def test_order_requires_existing_item_and_training(session):
    await add_user(session, 1)
    session.add(InventoryOrder(training_id=1, user_id=1, item_id=1, status="active", created_at=NOW))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_ensure_cleat_compat_is_idempotent(session):
    await ensure_cleat_compat(session)
    await ensure_cleat_compat(session)
    pairs = {(r.cleat_type, r.pedal_type) for r in await session.scalars(select(CleatCompat))}
    assert pairs == {("none", "flat"), ("spd", "spd"), ("look", "look"), ("spd_sl", "spd_sl")}


async def test_order_can_be_created(session):
    trainer = await add_user(session, 10, "trainer")
    rider = await add_user(session, 1)
    level = await add_level(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20))
    item = await add_item(session, "helmet", "Шлем №2", helmet_size="M")
    session.add(InventoryOrder(training_id=t.id, user_id=rider.telegram_id, item_id=item.id, status="active", created_at=NOW))
    await session.commit()
