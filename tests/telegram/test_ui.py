from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.telegram.ui import markup, short_title
from tests.factories import add_level, add_training, add_user


def test_markup_builds_callback_buttons():
    m = markup([("A", "a:1"), ("B", "b:2")], [("C", "c:3")])
    assert [[b.callback_data for b in row] for row in m.inline_keyboard] == [["a:1", "b:2"], ["c:3"]]
    assert m.inline_keyboard[1][0].text == "C"


async def test_short_title(session):
    trainer = await add_user(session, 1, "trainer")
    level = await add_level(session)
    t = await add_training(
        session, trainer=trainer, levels=[level], starts_at=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    )
    assert short_title(t, ZoneInfo("Europe/Moscow")) == "вторник, 20.10 19:00–20:30 · Интервалы"
