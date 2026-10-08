import pytest
from sqlalchemy.exc import IntegrityError

from bot.billing.models import Charge, ChargeKind, ChargeStatus, PriceRule
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_level, add_training, add_user


async def test_charge_is_unique_per_training_user_kind_and_ref(session):
    trainer = await add_user(session, 10, "trainer")
    await add_user(session, 1)
    level = await add_level(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW, status=TrainingStatus.DONE)

    def charge(**kw):
        base = dict(user_id=1, training_id=t.id, kind=ChargeKind.SESSION, ref_id=0, amount=100,
                    status=ChargeStatus.PENDING, created_at=NOW)
        base.update(kw)
        return Charge(**base)

    session.add_all([charge(), charge(kind=ChargeKind.INVENTORY, ref_id=1), charge(kind=ChargeKind.INVENTORY, ref_id=2)])
    await session.commit()
    session.add(charge())
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_price_fields_default_to_none(session):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW)
    await session.refresh(t)
    assert (t.single_price, t.surcharge_per_person) == (None, None)
    rule = PriceRule(kind="road", single_price=70000)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    assert (rule.single_price, rule.surcharge_per_person) == (70000, None)
