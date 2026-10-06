from datetime import timedelta

import pytest

from bot.core.errors import NotFound, PermissionDenied
from bot.participation.models import Participation
from bot.participation.service import JoinOutcome, ParticipationService
from tests.factories import NOW, add_level, add_training, add_user


async def setup(session, capacity=1):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20),
        capacity=capacity, published_at=NOW - timedelta(hours=1),
    )
    return trainer, t


async def test_manual_add_records_who_added(session):
    trainer, t = await setup(session, capacity=5)
    await add_user(session, 1)
    svc = ParticipationService(session)
    assert await svc.manual_add(trainer, t.id, 1, NOW) == JoinOutcome.JOINED
    row = await session.get(Participation, (t.id, 1))
    assert row.status == "going" and row.added_by == 10
    assert await svc.manual_add(trainer, t.id, 1, NOW) == JoinOutcome.ALREADY


async def test_manual_add_over_limit_needs_confirmation(session):
    trainer, t = await setup(session, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    await svc.manual_add(trainer, t.id, 1, NOW)
    assert await svc.manual_add(trainer, t.id, 2, NOW) == JoinOutcome.OVER_LIMIT
    assert await session.get(Participation, (t.id, 2)) is None
    assert await svc.manual_add(trainer, t.id, 2, NOW, allow_over_limit=True) == JoinOutcome.JOINED
    assert await svc.going_count(t.id) == 2


async def test_manual_add_requires_rights_and_known_user(session):
    trainer, t = await setup(session)
    stranger = await add_user(session, 5, "trainer")
    await add_user(session, 1)
    svc = ParticipationService(session)
    with pytest.raises(PermissionDenied):
        await svc.manual_add(stranger, t.id, 1, NOW)
    with pytest.raises(NotFound):
        await svc.manual_add(trainer, t.id, 777, NOW)


async def test_manual_remove(session):
    trainer, t = await setup(session, capacity=5)
    await add_user(session, 1)
    svc = ParticipationService(session)
    assert await svc.manual_remove(trainer, t.id, 1, NOW) is False  # не был записан
    await svc.manual_add(trainer, t.id, 1, NOW)
    assert await svc.manual_remove(trainer, t.id, 1, NOW) is True
    assert await svc.going_count(t.id) == 0


async def test_concurrent_manual_adds_respect_capacity(session, session_factory):
    trainer, t = await setup(session, capacity=1)
    for uid in (1, 2, 3, 4):
        await add_user(session, uid)

    async def add(uid):
        async with session_factory() as s:
            return await ParticipationService(s).manual_add(trainer, t.id, uid, NOW)

    import asyncio

    outcomes = await asyncio.gather(*(add(uid) for uid in (1, 2, 3, 4)))
    assert outcomes.count(JoinOutcome.JOINED) == 1
    assert outcomes.count(JoinOutcome.OVER_LIMIT) == 3
    assert await ParticipationService(session).going_count(t.id) == 1


async def test_manual_add_of_decliner_records_who_added(session):
    trainer, t = await setup(session, capacity=5)
    await add_user(session, 1)
    svc = ParticipationService(session)
    await svc.decline(t.id, 1, NOW)
    assert await svc.manual_add(trainer, t.id, 1, NOW + timedelta(minutes=1)) == JoinOutcome.JOINED
    row = await session.get(Participation, (t.id, 1))
    assert (row.status, row.added_by, row.updated_at) == ("going", 10, NOW + timedelta(minutes=1))


async def test_concurrent_manual_removes_report_one_removal(session, session_factory):
    import asyncio

    trainer, t = await setup(session, capacity=5)
    await add_user(session, 1)
    await ParticipationService(session).manual_add(trainer, t.id, 1, NOW)

    async def remove():
        async with session_factory() as s:
            return await ParticipationService(s).manual_remove(trainer, t.id, 1, NOW)

    assert sorted(await asyncio.gather(remove(), remove(), remove())) == [False, False, True]
