from datetime import timedelta

import pytest

from bot.core.errors import PermissionDenied
from bot.participation.service import JoinOutcome
from bot.publishing.service import PublishService
from bot.usecases import cancel_trainings, decline_training, join_training
from tests.factories import NOW, add_chat, add_level, add_training, add_user


async def published_training(session, messenger, capacity=1):
    await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer")
    level = await add_level(session)
    await add_chat(session, level, -1001)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=NOW + timedelta(hours=20), capacity=capacity
    )
    await PublishService(session, messenger, send_delay=0).publish(t.id, NOW)
    return t


async def test_join_button_flow_updates_the_post(session, messenger):
    t = await published_training(session, messenger)
    await add_user(session, 1)
    assert await join_training(session, messenger, t.id, 1, NOW) == JoinOutcome.JOINED
    assert "Идут: 1/1" in messenger.edits[-1].text


async def test_full_or_repeated_join_does_not_touch_the_post(session, messenger):
    t = await published_training(session, messenger)
    await add_user(session, 1)
    await add_user(session, 2)
    await join_training(session, messenger, t.id, 1, NOW)
    edits = len(messenger.edits)
    assert await join_training(session, messenger, t.id, 2, NOW) == JoinOutcome.FULL
    assert await join_training(session, messenger, t.id, 1, NOW) == JoinOutcome.ALREADY
    assert len(messenger.edits) == edits


async def test_decline_frees_the_seat_in_the_post(session, messenger):
    t = await published_training(session, messenger)
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    assert await decline_training(session, messenger, t.id, 1, NOW) == "going"
    assert "Идут: 0/1" in messenger.edits[-1].text


async def test_cancel_announces_to_posts_and_going_users(session, messenger):
    t = await published_training(session, messenger, capacity=5)
    admin = await add_user(session, 98, "admin")
    await add_user(session, 1)
    await join_training(session, messenger, t.id, 1, NOW)
    await cancel_trainings(session, messenger, admin, [t.id], NOW)
    assert "Отменена" in messenger.edits[-1].text
    assert (1, ) == tuple(uid for uid, text in messenger.notifications if "отменена" in text)


async def test_cancel_by_stranger_changes_nothing(session, messenger):
    t = await published_training(session, messenger)
    member = await add_user(session, 5, "member")
    edits = len(messenger.edits)
    with pytest.raises(PermissionDenied):
        await cancel_trainings(session, messenger, member, [t.id], NOW)
    assert len(messenger.edits) == edits
