from datetime import timedelta

from bot.participation.service import ParticipationService
from bot.publishing.service import PublishService
from bot.schedule.models import TrainingStatus
from tests.factories import NOW, add_chat, add_level, add_training, add_user

STARTS = NOW + timedelta(hours=24)


async def world(session, chat_id=-1001234567890):
    await add_user(session, 99, "admin")
    trainer = await add_user(session, 10, "trainer", name="Иван")
    level = await add_level(session)
    await add_chat(session, level, chat_id)
    return trainer, level


async def test_post_link_for_supergroup_and_none_otherwise(session, messenger):
    trainer, level = await world(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    svc = PublishService(session, messenger, send_delay=0)
    assert await svc.post_link(t.id) is None  # ещё не опубликована
    await svc.publish(t.id, NOW)
    post_id = messenger.sent[0].message_id
    assert await svc.post_link(t.id) == f"https://t.me/c/1234567890/{post_id}"


async def test_post_link_is_none_for_basic_group(session, messenger):
    trainer, level = await world(session, chat_id=-555)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    svc = PublishService(session, messenger, send_delay=0)
    await svc.publish(t.id, NOW)
    assert await svc.post_link(t.id) is None


async def test_announce_replaced_notifies_with_link_to_the_new_post(session, messenger):
    trainer, level = await world(session)
    old = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    new = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS + timedelta(days=7))
    svc = PublishService(session, messenger, send_delay=0)
    await svc.publish(old.id, NOW)
    await svc.publish(new.id, NOW)
    await add_user(session, 1)
    await ParticipationService(session).join(old.id, 1, NOW)
    old.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc.announce_replaced(old.id, new.id)
    (uid, text), = [n for n in messenger.notifications if n[0] == 1]
    old_post_id, new_post_id = messenger.sent[0].message_id, messenger.sent[1].message_id
    assert old_post_id != new_post_id
    assert "изменена" in text and f"https://t.me/c/1234567890/{new_post_id}" in text
    assert f"https://t.me/c/1234567890/{old_post_id}" not in text
    assert "Отменена" in messenger.edits[-1].text


async def test_announce_replaced_without_pair_is_a_plain_cancellation(session, messenger):
    trainer, level = await world(session)
    old = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    svc = PublishService(session, messenger, send_delay=0)
    await svc.publish(old.id, NOW)
    await add_user(session, 1)
    await ParticipationService(session).join(old.id, 1, NOW)
    old.status = TrainingStatus.CANCELLED
    await session.commit()
    await svc.announce_replaced(old.id, None)
    assert [text for uid, text in messenger.notifications if uid == 1][0].startswith("❌ Тренировка отменена")


async def test_notify_manual_change_ignores_unreachable_user(session, messenger):
    trainer, level = await world(session)
    t = await add_training(session, trainer=trainer, levels=[level], starts_at=STARTS)
    await add_user(session, 1)
    messenger.unreachable.add(1)
    svc = PublishService(session, messenger, send_delay=0)
    await svc.notify_manual_change(t.id, 1, added=True)  # не бросает
    assert messenger.notifications == []
    messenger.unreachable.clear()
    await svc.notify_manual_change(t.id, 1, added=False)
    assert "сн" in messenger.notifications[0][1]
