from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.inventory.flow import MyEntry, MyOrder, can_confirm, cancel_buttons, format_review, kinds_markup_data, render_my
from bot.inventory.matching import MatchStatus, Suggestion


def sug(kind, status, item_id, text):
    return Suggestion(kind, status, item_id, text)


def test_kinds_markup_marks_selected():
    rows = kinds_markup_data({"bike", "helmet"})
    assert ("✅ Велосипед", "iv:k:bike") in rows and ("▫️ Педали", "iv:k:pedals") in rows
    assert rows[-1] == ("Готово", "iv:kdone")


def test_review_text_and_confirmability():
    items = [sug("bike", MatchStatus.EXACT, 1, "🚲 Велосипед №1"),
             sug("helmet", MatchStatus.NEAREST, 2, "⛑ Шлем №2: размер L"),
             sug("shoes", MatchStatus.NONE, None, "👟 Туфли: нет в наличии")]
    text = format_review(items, [], replacement=False)
    assert "Велосипед №1" in text and "размер L" in text
    assert "ближайшие" in text
    assert "Подтвердить" in text
    assert can_confirm(items) is True
    assert can_confirm([items[2]]) is False


def test_review_for_replacement_mentions_it_and_keeps_confirmed():
    items = [sug("bike", MatchStatus.EXACT, 3, "🚲 Велосипед №3")]
    text = format_review(items, ["Шлем №2"], replacement=True)
    assert "забрали" in text and "Шлем №2" in text


def test_render_my():
    assert render_my([]) == "Записей пока нет. Расписание: /schedule"
    text = render_my([
        MyEntry("вторник, 20.10 19:00–20:30 · Шоссе", "идёте", ["Велосипед №7", "Шлем №2"]),
        MyEntry("четверг, 22.10 19:00–20:30 · Коферайд", "не идёте", []),
    ])
    assert "Шоссе" in text and "идёте" in text and "Велосипед №7" in text and "не идёте" in text


def test_cancel_buttons_name_the_training_and_match_listed_lines():
    tz = ZoneInfo("Europe/Moscow")
    tue = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    thu = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)
    later = datetime(2026, 12, 1, 16, 0, tzinfo=timezone.utc)
    orders = [
        MyOrder(order_id=1, training_id=10, starts_at=tue, label="Шлем №2"),
        MyOrder(order_id=2, training_id=11, starts_at=thu, label="Шлем №2"),
        MyOrder(order_id=3, training_id=99, starts_at=later, label="Велосипед №7"),  # тренировки нет в списке
    ]
    assert cancel_buttons(orders, listed_training_ids={10, 11}, tz=tz) == [
        ("🗑 20.10 19:00 · Шлем №2", "my:co:1"),
        ("🗑 22.10 19:00 · Шлем №2", "my:co:2"),
    ]
    assert cancel_buttons(orders, listed_training_ids=set(), tz=tz) == []  # «Записей пока нет» — без кнопок
