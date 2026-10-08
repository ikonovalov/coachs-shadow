import pytest

from bot.billing.models import Charge, ChargeStatus
from bot.billing.service import BillingService
from bot.core.errors import InvalidAction, PermissionDenied
from tests.billing.helpers import done_training, ensure_user, give_subscription
from tests.factories import NOW


async def setup(session):
    t = await done_training(session, going=[1])
    created = await BillingService(session).charge_training(t.id, NOW)
    admin = await ensure_user(session, 99, "admin")
    return t, created[0], admin


async def test_mark_paid_is_admin_only_and_not_repeatable(session):
    t, charge, admin = await setup(session)
    svc = BillingService(session)
    for actor in (await ensure_user(session, 1), await ensure_user(session, 10, "trainer")):
        with pytest.raises(PermissionDenied):
            await svc.mark_charge_paid(actor, charge.id, NOW)
    paid = await svc.mark_charge_paid(admin, charge.id, NOW)
    assert (paid.status, paid.paid_at, paid.paid_by) == (ChargeStatus.PAID, NOW, 99)
    with pytest.raises(InvalidAction):
        await svc.mark_charge_paid(admin, charge.id, NOW)  # двойной тап


async def test_covered_and_void_charges_cannot_be_marked_paid(session):
    t, charge, admin = await setup(session)
    charge.status = ChargeStatus.VOID
    await session.commit()
    with pytest.raises(InvalidAction):
        await BillingService(session).mark_charge_paid(admin, charge.id, NOW)


async def test_mark_user_paid_closes_charges_and_subscriptions(session):
    t, charge, admin = await setup(session)
    unpaid = await give_subscription(session, 1, [t.series_id], paid=False)
    svc = BillingService(session)
    assert await svc.mark_user_paid(admin, 1, NOW) == 2
    assert (await session.get(Charge, charge.id)).status == ChargeStatus.PAID
    await session.refresh(unpaid)
    assert unpaid.paid_at == NOW
    assert await svc.mark_user_paid(admin, 1, NOW) == 0


async def test_mark_subscription_paid_once(session):
    t, _, admin = await setup(session)
    sub = await give_subscription(session, 1, [t.series_id], paid=False)
    svc = BillingService(session)
    assert (await svc.mark_subscription_paid(admin, sub.id, NOW)).paid_by == 99
    with pytest.raises(InvalidAction):
        await svc.mark_subscription_paid(admin, sub.id, NOW)


async def test_refund_only_for_void_charges_marked_due(session):
    t, charge, admin = await setup(session)
    svc = BillingService(session)
    with pytest.raises(InvalidAction):
        await svc.mark_refunded(admin, charge.id, NOW)  # не к возврату
    charge.status, charge.refund_due = ChargeStatus.VOID, True
    await session.commit()
    done = await svc.mark_refunded(admin, charge.id, NOW)
    assert done.refunded_at == NOW
    with pytest.raises(InvalidAction):
        await svc.mark_refunded(admin, charge.id, NOW)
    with pytest.raises(PermissionDenied):
        await svc.mark_refunded(await ensure_user(session, 1), charge.id, NOW)
