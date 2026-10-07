from datetime import date, time

import pytest

from bot.core.errors import InvalidAction, PermissionDenied
from bot.schedule.models import TrainingSeries
from bot.schedule.service import ScheduleService, SeriesInput
from bot.telegram.handlers import trainer
from tests.factories import NOW, add_chat, add_chat_trainer, add_level, add_user
from tests.telegram.fakes import FakeCallback, callback_data


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(trainer, "utcnow", lambda: NOW)


async def setup_series(session):
    """Серии по вторникам: a у тренера 10, b у тренера 11 и одна разовая у тренера 10."""
    admin = await add_user(session, 99, "admin")
    mine = await add_user(session, 10, "trainer")
    other = await add_user(session, 11, "trainer")
    level = await add_level(session)
    chat = await add_chat(session, level, -1001)
    await add_chat_trainer(session, chat, 10)
    await add_chat_trainer(session, chat, 11)
    svc = ScheduleService(session)

    def data(place, trainer_id, weekdays):
        return SeriesInput(
            kind="intervals", place=place, level_ids=[level.id], start_date=date(2026, 10, 20),
            start_time=time(19, 0), weekdays=weekdays, trainer_id=trainer_id,
        )

    a = await svc.create_series(admin, data("Парк", 10, {2}), NOW)
    b = await svc.create_series(admin, data("Стадион", 11, {2}), NOW)
    await svc.create_series(admin, data("Разовая", 10, set()), NOW)
    return admin, mine, other, a, b


async def test_list_shows_own_active_series_only(session):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback("tr:slist")
    await trainer.on_series_list(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert text == "Мои серии"
    assert callback_data(markup) == [f"tr:series:{a.id}", "tr:menu"]
    assert cb.answers == [(None, False)]


async def test_admin_list_shows_all_series_without_one_offs(session):
    admin, _, _, a, b = await setup_series(session)
    cb = FakeCallback("tr:slist")
    await trainer.on_series_list(cb, session, admin)
    assert callback_data(cb.message.edits[-1][1]) == [f"tr:series:{a.id}", f"tr:series:{b.id}", "tr:menu"]


async def test_empty_list_after_series_is_ended(session):
    admin, mine, _, a, _ = await setup_series(session)
    await ScheduleService(session).end_series(admin, a.id, NOW)
    cb = FakeCallback("tr:slist")
    await trainer.on_series_list(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert text == "Активных серий нет"
    assert callback_data(markup) == ["tr:menu"]


async def test_series_screen_has_summary_end_button_and_back_to_list(session):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback(f"tr:series:{a.id}")
    await trainer.on_series_menu(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert "Место: Парк" in text and "Начало: 20.10.2026" in text
    data = callback_data(markup)
    assert f"tr:send:{a.id}" in data and f"tr:sedit:{a.id}:place" in data
    assert data[-1] == "tr:slist"


async def test_ended_series_screen_has_no_end_button(session):
    admin, mine, _, a, _ = await setup_series(session)
    await ScheduleService(session).end_series(admin, a.id, NOW)
    cb = FakeCallback(f"tr:series:{a.id}")
    await trainer.on_series_menu(cb, session, mine)
    assert not any(d.startswith("tr:send:") for d in callback_data(cb.message.edits[-1][1]))


async def test_series_screen_of_foreign_trainer_is_denied(session):
    _, mine, _, _, b = await setup_series(session)
    with pytest.raises(PermissionDenied):
        await trainer.on_series_menu(FakeCallback(f"tr:series:{b.id}"), session, mine)


async def test_end_ask_shows_count_and_confirm_buttons(session):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback(f"tr:send:{a.id}")
    await trainer.on_series_end_ask(cb, session, mine)
    text, markup = cb.message.edits[-1]
    assert "Будущие тренировки (4)" in text and "Парк" in text
    assert callback_data(markup) == [f"tr:sendok:{a.id}", f"tr:series:{a.id}"]


async def test_end_do_ends_series_and_reports_count(session, messenger):
    _, mine, _, a, _ = await setup_series(session)
    cb = FakeCallback(f"tr:sendok:{a.id}")
    await trainer.on_series_end_do(cb, session, mine, messenger)
    text, markup = cb.message.edits[-1]
    assert text == "Серия завершена. Отменено тренировок: 4"
    assert callback_data(markup) == ["tr:slist"]
    await session.refresh(a)
    assert a.end_date == date(2026, 10, 18)


async def test_end_do_by_foreign_trainer_changes_nothing(session, messenger):
    _, mine, _, _, b = await setup_series(session)
    with pytest.raises(PermissionDenied):
        await trainer.on_series_end_do(FakeCallback(f"tr:sendok:{b.id}"), session, mine, messenger)
    await session.refresh(b)
    assert b.end_date is None


async def test_end_do_twice_is_rejected(session, messenger):
    _, mine, _, a, _ = await setup_series(session)
    await trainer.on_series_end_do(FakeCallback(f"tr:sendok:{a.id}"), session, mine, messenger)
    with pytest.raises(InvalidAction):
        await trainer.on_series_end_do(FakeCallback(f"tr:sendok:{a.id}"), session, mine, messenger)
