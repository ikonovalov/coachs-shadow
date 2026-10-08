from datetime import timedelta

import pytest

from bot.billing.models import Charge, ChargeKind, ChargeStatus, Subscription
from bot.billing.service import ATTENDANCE_WINDOW, BillingService
from bot.core.errors import InvalidAction, NotFound, PermissionDenied
from tests.billing.helpers import STARTS, done_training, ensure_user, give_subscription
from tests.factories import NOW
from sqlalchemy import select


async def charged(session, **kw):
    t = await done_training(session, **kw)
    await BillingService(session).charge_training(t.id, NOW)
    trainer = await ensure_user(session, 10, "trainer")
    return t, trainer


async def of(session, training_id, user_id):
    rows = await session.scalars(
        select(Charge).where(Charge.training_id == training_id, Charge.user_id == user_id).order_by(Charge.kind)
    )
    return list(rows)


async def test_absent_with_unpaid_charge_voids_it(session):
    t, trainer = await charged(session, going=[1, 2])
    result = await BillingService(session).set_attendance(trainer, t.id, 1, False, NOW)
    assert result.changed and not result.refund_due and result.returned_to is None
    assert [c.status for c in await of(session, t.id, 1)] == [ChargeStatus.VOID]
    assert [c.status for c in await of(session, t.id, 2)] == [ChargeStatus.PENDING]


async def test_absent_returns_the_unit_to_the_subscription(session):
    t, trainer = await charged(session, going=[1])
    sub = await give_subscription(session, 1, [t.series_id], name="Утро")
    await session.execute(Charge.__table__.delete())
    await session.commit()
    await BillingService(session).charge_training(t.id, NOW)
    assert (await session.get(Subscription, sub.id)).sessions_left == 3
    result = await BillingService(session).set_attendance(trainer, t.id, 1, False, NOW)
    assert result.returned_to == "Утро"
    assert (await session.get(Subscription, sub.id)).sessions_left == 4


async def test_absent_after_payment_marks_refund_due(session):
    t, trainer = await charged(session, going=[1])
    admin = await ensure_user(session, 99, "admin")
    charge = (await of(session, t.id, 1))[0]
    await BillingService(session).mark_charge_paid(admin, charge.id, NOW)
    result = await BillingService(session).set_attendance(trainer, t.id, 1, False, NOW)
    assert result.refund_due
    row = (await of(session, t.id, 1))[0]
    assert (row.status, row.refund_due, row.refunded_at) == (ChargeStatus.VOID, True, None)


async def test_marking_the_same_state_twice_is_a_noop(session):
    t, trainer = await charged(session, going=[1])
    svc = BillingService(session)
    assert (await svc.set_attendance(trainer, t.id, 1, True, NOW)).changed is False
    assert (await svc.set_attendance(trainer, t.id, 1, False, NOW)).changed is True
    assert (await svc.set_attendance(trainer, t.id, 1, False, NOW)).changed is False


async def test_present_again_after_unpaid_void_recharges(session):
    t, trainer = await charged(session, going=[1])
    svc = BillingService(session)
    await svc.set_attendance(trainer, t.id, 1, False, NOW)
    await svc.set_attendance(trainer, t.id, 1, True, NOW)
    assert [(c.status, c.amount) for c in await of(session, t.id, 1)] == [(ChargeStatus.PENDING, 70000)]


async def test_present_again_after_covered_void_takes_the_unit_again(session):
    t, trainer = await charged(session, going=[])
    await ensure_user(session, 1)
    sub = await give_subscription(session, 1, [t.series_id])
    svc = BillingService(session)
    await svc.set_attendance(trainer, t.id, 1, True, NOW)  # пришёл без записи: списание с абонемента
    assert (await session.get(Subscription, sub.id)).sessions_left == 3
    await svc.set_attendance(trainer, t.id, 1, False, NOW)
    assert (await session.get(Subscription, sub.id)).sessions_left == 4
    await svc.set_attendance(trainer, t.id, 1, True, NOW)
    assert (await session.get(Subscription, sub.id)).sessions_left == 3
    assert [c.status for c in await of(session, t.id, 1)] == [ChargeStatus.COVERED]


async def test_present_again_after_paid_void_restores_paid_status(session):
    t, trainer = await charged(session, going=[1])
    admin = await ensure_user(session, 99, "admin")
    svc = BillingService(session)
    await svc.mark_charge_paid(admin, (await of(session, t.id, 1))[0].id, NOW)
    await svc.set_attendance(trainer, t.id, 1, False, NOW)
    await svc.set_attendance(trainer, t.id, 1, True, NOW)
    row = (await of(session, t.id, 1))[0]
    assert (row.status, row.refund_due) == (ChargeStatus.PAID, False)


async def test_present_again_after_refund_owes_again(session):
    t, trainer = await charged(session, going=[1])
    admin = await ensure_user(session, 99, "admin")
    svc = BillingService(session)
    charge = (await of(session, t.id, 1))[0]
    await svc.mark_charge_paid(admin, charge.id, NOW)
    await svc.set_attendance(trainer, t.id, 1, False, NOW)
    await svc.mark_refunded(admin, charge.id, NOW)
    await svc.set_attendance(trainer, t.id, 1, True, NOW)
    row = (await of(session, t.id, 1))[0]
    assert (row.status, row.refund_due, row.refunded_at, row.paid_at) == (ChargeStatus.PENDING, False, None, None)


async def test_walk_in_without_registration_is_charged(session):
    t, trainer = await charged(session, going=[1])
    await ensure_user(session, 5)
    result = await BillingService(session).set_attendance(trainer, t.id, 5, True, NOW)
    assert result.changed
    assert [(c.status, c.amount) for c in await of(session, t.id, 5)] == [(ChargeStatus.PENDING, 70000)]
    assert 5 in await BillingService(session).attendee_ids(t.id)


async def test_surcharge_is_recomputed_for_unpaid_and_reported_for_paid(session):
    t, trainer = await charged(session, price=0, surcharge=50000, going=[1, 2, 3])
    admin = await ensure_user(session, 99, "admin")
    svc = BillingService(session)
    await svc.mark_charge_paid(admin, (await of(session, t.id, 2))[0].id, NOW)  # 2 уже заплатил 666,67
    result = await svc.set_attendance(trainer, t.id, 3, False, NOW)  # осталось двое: 50000*3/2 = 75000
    assert result.pending_changes == [(1, 75000)]
    assert result.paid_diffs == [(2, 66667, 75000)]
    assert [c.amount for c in await of(session, t.id, 1)] == [75000]
    assert [c.amount for c in await of(session, t.id, 2)] == [66667]  # оплаченное не трогаем
    assert [c.status for c in await of(session, t.id, 3)] == [ChargeStatus.VOID]


async def test_walk_in_changes_everyones_surcharge_and_gets_a_charge(session):
    t, trainer = await charged(session, price=0, surcharge=50000, going=[1, 2])
    await ensure_user(session, 5)
    result = await BillingService(session).set_attendance(trainer, t.id, 5, True, NOW)
    assert sorted(result.pending_changes) == [(1, 66667), (2, 66667)]
    assert [c.amount for c in await of(session, t.id, 5)] == [66667]


async def test_only_managers_of_the_training_may_mark(session):
    t, trainer = await charged(session, going=[1])
    svc = BillingService(session)
    other = await ensure_user(session, 11, "trainer")
    member = await ensure_user(session, 1)
    for actor in (other, member):
        with pytest.raises(PermissionDenied):
            await svc.set_attendance(actor, t.id, 1, False, NOW)
    admin = await ensure_user(session, 99, "admin")
    assert (await svc.set_attendance(admin, t.id, 1, False, NOW)).changed


async def test_window_closes_for_trainer_but_not_for_admin(session):
    t, trainer = await charged(session, going=[1, 2])
    admin = await ensure_user(session, 99, "admin")
    late = t.ends_at + ATTENDANCE_WINDOW + timedelta(minutes=1)
    svc = BillingService(session)
    with pytest.raises(InvalidAction):
        await svc.set_attendance(trainer, t.id, 1, False, late)
    assert (await svc.set_attendance(trainer, t.id, 1, False, t.ends_at + ATTENDANCE_WINDOW)).changed
    assert (await svc.set_attendance(admin, t.id, 2, False, late)).changed


async def test_unknown_training_user_and_not_started_training(session):
    t, trainer = await charged(session, going=[1])
    svc = BillingService(session)
    with pytest.raises(NotFound):
        await svc.set_attendance(trainer, 9999, 1, False, NOW)
    with pytest.raises(NotFound):
        await svc.set_attendance(trainer, t.id, 777, True, NOW)
    t.status = "scheduled"
    await session.commit()
    with pytest.raises(InvalidAction):
        await svc.set_attendance(trainer, t.id, 1, False, NOW)
