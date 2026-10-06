from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from bot.participation.models import Participation
from bot.schedule.models import Training
from bot.users.models import ChatTrainer
from tests.factories import add_chat, add_chat_trainer, add_level, add_training, add_user


async def test_datetime_roundtrip_is_aware_utc(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    starts = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=starts)
    tid = t.id
    session.expire_all()
    fresh = await session.get(Training, tid)
    assert fresh.starts_at == starts
    assert fresh.starts_at.tzinfo is not None


async def test_only_one_lead_per_chat(session):
    level = await add_level(session)
    chat = await add_chat(session, level)
    await add_user(session, 1, "trainer")
    await add_user(session, 2, "trainer")
    await add_chat_trainer(session, chat, 1, is_lead=True)
    session.add(ChatTrainer(chat_id=chat.chat_id, user_id=2, is_lead=True))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_many_non_lead_trainers_allowed(session):
    level = await add_level(session)
    chat = await add_chat(session, level)
    for uid in (1, 2, 3):
        await add_user(session, uid, "trainer")
    await add_chat_trainer(session, chat, 1, is_lead=True)
    await add_chat_trainer(session, chat, 2)
    await add_chat_trainer(session, chat, 3)


async def test_participation_is_unique_per_training_and_user(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
    )
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    session.add(Participation(training_id=t.id, user_id=1, status="going", updated_at=now))
    await session.commit()
    session.add(Participation(training_id=t.id, user_id=1, status="declined", updated_at=now))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_foreign_keys_are_enforced(session):
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    session.add(Participation(training_id=999, user_id=999, status="going", updated_at=now))
    with pytest.raises(IntegrityError):
        await session.commit()
