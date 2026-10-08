from datetime import timedelta

from sqlalchemy import select

from bot.billing.models import Subscription, SubscriptionSeries
from bot.participation.models import Participation, ParticipationStatus
from bot.schedule.models import Level, Training, TrainingStatus
from bot.users.models import User
from tests.factories import NOW, add_level, add_training, add_user

STARTS = NOW - timedelta(hours=2)  # тренировка 90 минут, закончилась 30 минут назад


async def ensure_user(session, uid, role="member") -> User:
    return await session.get(User, uid) or await add_user(session, uid, role)


async def ensure_level(session) -> Level:
    return (await session.scalars(select(Level))).first() or await add_level(session)


async def done_training(session, *, price=70000, surcharge=None, going=(), starts_at=STARTS, kind="road") -> Training:
    trainer = await ensure_user(session, 10, "trainer")
    level = await ensure_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=starts_at, status=TrainingStatus.DONE,
        published_at=starts_at - timedelta(hours=24),
    )
    t.single_price, t.surcharge_per_person, t.kind = price, surcharge, kind
    for i, uid in enumerate(going):
        await ensure_user(session, uid)
        session.add(Participation(
            training_id=t.id, user_id=uid, status=ParticipationStatus.GOING, updated_at=NOW - timedelta(hours=5, minutes=-i),
        ))
    await session.commit()
    return t


async def sibling_training(session, base: Training, starts_at, going=()) -> Training:
    """Ещё одна тренировка той же серии."""
    t = Training(
        series_id=base.series_id, starts_at=starts_at, duration_min=base.duration_min, trainer_id=base.trainer_id,
        place=base.place, kind=base.kind, description="", capacity=None, status=TrainingStatus.DONE,
        published_at=starts_at - timedelta(hours=24), single_price=base.single_price,
        surcharge_per_person=base.surcharge_per_person, levels=list(base.levels),
    )
    session.add(t)
    await session.flush()
    for uid in going:
        await ensure_user(session, uid)
        session.add(Participation(training_id=t.id, user_id=uid, status=ParticipationStatus.GOING, updated_at=NOW))
    await session.commit()
    return t


async def give_subscription(
    session, user_id, series_ids, *, sessions=4, left=None, expires_at=None, name="Абонемент", price=300000, paid=True
) -> Subscription:
    await ensure_user(session, user_id)
    await ensure_user(session, 99, "admin")
    sub = Subscription(
        user_id=user_id, name=name, sessions_total=sessions, sessions_left=sessions if left is None else left,
        starts_at=NOW - timedelta(days=30), expires_at=expires_at, price=price,
        paid_at=NOW if paid else None, issued_by=99, created_at=NOW - timedelta(days=30),
    )
    session.add(sub)
    await session.flush()
    for sid in series_ids:
        session.add(SubscriptionSeries(subscription_id=sub.id, series_id=sid))
    await session.commit()
    return sub
