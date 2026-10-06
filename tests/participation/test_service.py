from datetime import timedelta

import pytest

from bot.core.errors import InvalidAction, NotFound
from bot.participation.service import JoinOutcome, ParticipationService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_level, add_training, add_user


async def published(session, capacity=None, **kw):
    trainer = await add_user(session, 1000, "trainer")
    level = await add_level(session)
    return await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20),
        capacity=capacity, published_at=NOW - timedelta(hours=4), **kw
    )


async def test_join_and_count(session):
    t = await published(session, capacity=2)
    await add_user(session, 1)
    svc = ParticipationService(session)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.JOINED
    assert await svc.going_count(t.id) == 1


async def test_join_twice_is_idempotent(session):
    t = await published(session)
    await add_user(session, 1)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.ALREADY
    assert await svc.going_count(t.id) == 1


async def test_already_going_gets_already_even_when_full(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.ALREADY


async def test_full_training_rejects_newcomer_and_keeps_status(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.join(t.id, 2, NOW) == JoinOutcome.FULL
    going, declined = await svc.list_participants(t.id)
    assert [u.telegram_id for u in going] == [1] and declined == []


async def test_decliner_cannot_rejoin_a_full_training_and_stays_declined(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.join(t.id, 2, NOW)
    await svc.decline(t.id, 1, NOW)
    assert await svc.join(t.id, 1, NOW) == JoinOutcome.FULL
    _, declined = await svc.list_participants(t.id)
    assert [u.telegram_id for u in declined] == [1]


async def test_decline_frees_a_seat_and_reports_previous_status(session):
    t = await published(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.join(t.id, 1, NOW)
    assert await svc.decline(t.id, 1, NOW) == "going"
    assert await svc.join(t.id, 2, NOW) == JoinOutcome.JOINED
    assert await svc.decline(t.id, 1, NOW) == "declined"
    await add_user(session, 3)
    assert await svc.decline(t.id, 3, NOW) is None


async def test_no_signup_for_unpublished_cancelled_started_or_missing(session):
    trainer = await add_user(session, 1000, "trainer")
    level = await add_level(session)
    await add_user(session, 1)
    svc = ParticipationService(session)
    unpublished = await add_training(session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(days=3))
    cancelled = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20),
        published_at=NOW, status=TrainingStatus.CANCELLED,
    )
    started = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW - timedelta(minutes=5), published_at=NOW - timedelta(days=1)
    )
    for t in (unpublished, cancelled, started):
        with pytest.raises(InvalidAction):
            await svc.join(t.id, 1, NOW)
    with pytest.raises(NotFound):
        await svc.join(9999, 1, NOW)


async def test_list_participants_orders_by_time(session):
    t = await published(session)
    for uid in (1, 2, 3):
        await add_user(session, uid)
    svc = ParticipationService(session)
    await svc.join(t.id, 2, NOW)
    await svc.join(t.id, 1, NOW + timedelta(minutes=1))
    await svc.decline(t.id, 3, NOW)
    going, declined = await svc.list_participants(t.id)
    assert [u.telegram_id for u in going] == [2, 1]
    assert [u.telegram_id for u in declined] == [3]


async def test_concurrent_joins_never_exceed_capacity(session, session_factory):
    t = await published(session, capacity=1)
    for uid in (1, 2, 3, 4):
        await add_user(session, uid)

    async def join(uid):
        async with session_factory() as s:
            return await ParticipationService(s).join(t.id, uid, NOW)

    import asyncio

    outcomes = await asyncio.gather(*(join(uid) for uid in (1, 2, 3, 4)))
    assert outcomes.count(JoinOutcome.JOINED) == 1
    assert await ParticipationService(session).going_count(t.id) == 1
