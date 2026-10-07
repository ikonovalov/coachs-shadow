from datetime import timedelta

from sqlalchemy import select

from bot.participation.service import ParticipationService
from bot.publishing.models import TrainingPost
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingStatus
from tests.factories import NOW, add_chat, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=24)


async def world(session, chats=(-1001,), published_at=None):
    await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer", name="Иван")
    level = await add_level(session)
    for chat_id in chats:
        await add_chat(session, level, chat_id)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=STARTS, capacity=5, published_at=published_at
    )
    return t


def svc(session, messenger):
    return PublishService(session, messenger, send_delay=0)


async def posts(session):
    return list(await session.scalars(select(TrainingPost).order_by(TrainingPost.chat_id)))


async def test_publish_posts_to_every_chat_of_the_levels_once(session, messenger):
    t = await world(session, chats=(-1001, -1002))
    await svc(session, messenger).publish(t.id, NOW)
    assert sorted(m.chat_id for m in messenger.sent) == [-1002, -1001]
    assert "Шоссе" in messenger.sent[0].text and messenger.sent[0].keyboard  # buttons are live
    assert len(await posts(session)) == 2
    await session.refresh(t)
    assert t.published_at == NOW
    await svc(session, messenger).publish(t.id, NOW)  # rerun
    assert len(messenger.sent) == 2


async def test_publish_resumes_after_partial_failure_without_duplicates(session, messenger):
    t = await world(session, chats=(-1001, -1002))
    messenger.unavailable_chats.add(-1002)
    await svc(session, messenger).publish(t.id, NOW)
    assert [p.chat_id for p in await posts(session)] == [-1001]
    assert any("-1002" in text for uid, text in messenger.notifications if uid == 99)  # admin warned
    await session.refresh(t)
    assert t.published_at == NOW


async def test_publish_without_chats_still_opens_signup_and_warns_admin(session, messenger):
    t = await world(session, chats=())
    await svc(session, messenger).publish(t.id, NOW)
    await session.refresh(t)
    assert t.published_at == NOW
    assert messenger.sent == []
    assert [uid for uid, _ in messenger.notifications] == [99]


async def test_publish_skips_cancelled_and_started_trainings(session, messenger):
    t = await world(session)
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc(session, messenger).publish(t.id, NOW)
    assert messenger.sent == []
    t.status = TrainingStatus.SCHEDULED
    await session.commit()
    await svc(session, messenger).publish(t.id, STARTS + timedelta(minutes=1))
    assert messenger.sent == []


async def test_refresh_edits_posts_with_new_count(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    await add_user(session, 1, name="Анна")
    await ParticipationService(session).join(t.id, 1, NOW)
    await svc(session, messenger).refresh_posts(t.id)
    assert "Идут: 1/5" in messenger.edits[-1].text


async def test_refresh_replaces_manually_deleted_post_and_stores_new_message_id(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    (post,) = await posts(session)
    old_id = post.message_id
    messenger.deleted_posts.add((post.chat_id, old_id))
    await svc(session, messenger).refresh_posts(t.id)
    await session.refresh(post)
    assert post.message_id != old_id
    assert len(messenger.sent) == 2


async def test_refresh_survives_unavailable_chat(session, messenger):
    t = await world(session, chats=(-1001, -1002))
    await svc(session, messenger).publish(t.id, NOW)
    messenger.unavailable_chats.add(-1002)
    await svc(session, messenger).refresh_posts(t.id)
    assert [e.chat_id for e in messenger.edits] == [-1001]


async def test_close_marks_done_and_removes_buttons(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    await svc(session, messenger).close(t.id, STARTS - timedelta(minutes=1))  # too early: no-op
    await session.refresh(t)
    assert t.status == TrainingStatus.SCHEDULED
    await svc(session, messenger).close(t.id, STARTS)
    await session.refresh(t)
    assert t.status == TrainingStatus.DONE
    assert "Прошла" in messenger.edits[-1].text and messenger.edits[-1].keyboard == []


async def test_close_does_not_touch_cancelled(session, messenger):
    t = await world(session)
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc(session, messenger).close(t.id, STARTS)
    await session.refresh(t)
    assert t.status == TrainingStatus.CANCELLED


async def test_reminders_go_to_going_users_and_unreachable_ones_do_not_break(session, messenger):
    t = await world(session, published_at=NOW)
    for uid in (1, 2, 3):
        await add_user(session, uid)
    p = ParticipationService(session)
    await p.join(t.id, 1, NOW)
    await p.join(t.id, 2, NOW)
    await p.decline(t.id, 3, NOW)
    messenger.unreachable.add(2)  # never pressed /start
    await svc(session, messenger).send_reminders(t.id, STARTS - timedelta(hours=2))
    assert [uid for uid, _ in messenger.notifications] == [1]


async def test_no_reminder_after_start_or_for_cancelled(session, messenger):
    t = await world(session, published_at=NOW)
    await add_user(session, 1)
    await ParticipationService(session).join(t.id, 1, NOW)
    await svc(session, messenger).send_reminders(t.id, STARTS + timedelta(minutes=1))
    assert messenger.notifications == []


async def test_announce_cancelled_updates_posts_and_notifies_going(session, messenger):
    t = await world(session)
    await svc(session, messenger).publish(t.id, NOW)
    await add_user(session, 1)
    await add_user(session, 2)
    p = ParticipationService(session)
    await p.join(t.id, 1, NOW)
    await p.join(t.id, 2, NOW)
    messenger.unreachable.add(2)  # never pressed /start: must not break the update
    t.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc(session, messenger).announce_cancelled(t.id)
    assert "Отменена" in messenger.edits[-1].text and messenger.edits[-1].keyboard == []
    assert [uid for uid, _ in messenger.notifications if uid in (1, 2)] == [1]
