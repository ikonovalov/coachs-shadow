from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select

from bot.core.errors import InvalidAction
from bot.participation.service import JoinOutcome
from bot.publishing.service import PublishService
from bot.schedule.models import Training, TrainingStatus
from bot.schedule.service import ScheduleService, SeriesInput, TrainingEdit, series_to_input
from bot.usecases import (
    join_training, manual_add_participant, manual_remove_participant, update_series, update_training,
)
from tests.factories import add_chat, add_level, add_user

T0 = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(days=2)


async def weekly_published(session, messenger, capacity=2):
    admin = await add_user(session, 99, "admin")
    level = await add_level(session)
    await add_chat(session, level, -1001234567890)
    svc = ScheduleService(session)
    series = await svc.create_series(
        admin,
        SeriesInput(kind="intervals", place="Парк", level_ids=[level.id], weekdays={2},
                    start_date=date(2026, 10, 6), start_time=time(19, 0), capacity=capacity),
        T0,
    )
    t13 = await session.scalar(select(Training).where(Training.starts_at == datetime(2026, 10, 13, 16, tzinfo=timezone.utc)))
    await PublishService(session, messenger, send_delay=0).publish(t13.id, T1)
    return admin, svc, series, t13


async def test_series_edit_replaces_publishes_new_and_notifies_with_link(session, messenger):
    admin, svc, series, t13 = await weekly_published(session, messenger)
    await add_user(session, 1)
    await join_training(session, messenger, t13.id, 1, T1)
    result = await update_series(session, messenger, admin, series.id, replace(series_to_input(series), place="Стадион"), T1)
    assert len(result) == 1 and result[0].new_id is not None
    new = await session.get(Training, result[0].new_id)
    assert new.published_at is not None  # новая опубликована сразу
    msg = [text for uid, text in messenger.notifications if uid == 1][-1]
    old_post_id, new_post_id = messenger.sent[0].message_id, messenger.sent[1].message_id
    assert old_post_id != new_post_id
    assert "изменена" in msg and f"https://t.me/c/1234567890/{new_post_id}" in msg
    assert f"https://t.me/c/1234567890/{old_post_id}" not in msg


async def test_training_edit_refreshes_the_post_and_protects_going_users(session, messenger):
    admin, svc, series, t13 = await weekly_published(session, messenger, capacity=5)
    for uid in (1, 2, 3):
        await add_user(session, uid)
        await join_training(session, messenger, t13.id, uid, T1)
    await update_training(session, messenger, admin, t13.id, TrainingEdit(place="Склон"), T1)
    assert "Склон" in messenger.edits[-1].text
    with pytest.raises(InvalidAction, match="записано"):
        await update_training(session, messenger, admin, t13.id, TrainingEdit(capacity=2), T1)
    assert t13.capacity == 5


async def test_manual_add_flow_over_limit_and_notifications(session, messenger):
    admin, svc, series, t13 = await weekly_published(session, messenger, capacity=1)
    await add_user(session, 1)
    await add_user(session, 2)
    assert await manual_add_participant(session, messenger, admin, t13.id, 1, T1) == JoinOutcome.JOINED
    assert "Идут: 1/1" in messenger.edits[-1].text
    assert any(uid == 1 for uid, _ in messenger.notifications)
    edits = len(messenger.edits)
    assert await manual_add_participant(session, messenger, admin, t13.id, 2, T1) == JoinOutcome.OVER_LIMIT
    assert len(messenger.edits) == edits  # ничего не менялось
    assert await manual_add_participant(session, messenger, admin, t13.id, 2, T1, allow_over_limit=True) == JoinOutcome.JOINED
    assert "Идут: 2/1" in messenger.edits[-1].text
    assert await manual_remove_participant(session, messenger, admin, t13.id, 2, T1) is True
    assert "Идут: 1/1" in messenger.edits[-1].text
