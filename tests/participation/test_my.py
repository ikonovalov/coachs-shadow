from datetime import timedelta

from bot.participation.models import Participation
from bot.participation.service import ParticipationService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_level, add_training, add_user


async def test_my_upcoming_lists_future_scheduled_with_my_status(session):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_user(session, 1)
    await add_user(session, 2)
    svc = ParticipationService(session)
    mk = lambda hours, **kw: add_training(  # noqa: E731
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=hours),
        published_at=NOW - timedelta(hours=1), **kw)
    declined = await mk(50)
    going = await mk(30)
    past = await mk(-2)
    cancelled = await mk(70, status=TrainingStatus.CANCELLED)
    untouched = await mk(90)
    await svc.join(going.id, 1, NOW)
    await svc.decline(declined.id, 1, NOW)
    await svc.join(untouched.id, 2, NOW)  # чужая запись не попадает в мой список
    # через сервис на прошедшую/отменённую не записаться — кладём записи напрямую, чтобы проверить фильтры
    for training in (past, cancelled):
        session.add(Participation(training_id=training.id, user_id=1, status="going", updated_at=NOW))
    await session.commit()
    result = await svc.my_upcoming(1, NOW)
    assert [(t.id, s) for t, s in result] == [(going.id, "going"), (declined.id, "declined")]


async def test_my_upcoming_respects_limit(session):
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_user(session, 1)
    svc = ParticipationService(session)
    for hours in (30, 40, 50):
        training = await add_training(
            session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=hours),
            published_at=NOW - timedelta(hours=1))
        await svc.join(training.id, 1, NOW)
    assert len(await svc.my_upcoming(1, NOW, limit=2)) == 2
