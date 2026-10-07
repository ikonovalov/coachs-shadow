from dataclasses import replace
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.core.messenger import Button
from bot.publishing.render import CardData, fit_alert, render_card, render_participants

MSK = ZoneInfo("Europe/Moscow")


def card(**kw):
    base = dict(
        training_id=7, status="scheduled", starts_at=datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc),
        duration_min=90, kind="road", place="Парк Горького", trainer_name="Иван",
        level_names=["Новички", "Любители"], capacity=10, going_count=3, description="",
        published=True, opens_at=datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return CardData(**base)


def test_published_card_text_and_buttons():
    text, kb = render_card(card(description="Взять воду"), MSK)
    assert "Шоссе" in text
    assert "вторник, 20.10 19:00–20:30" in text  # shown in club time
    assert "Парк Горького" in text and "Иван" in text and "Новички, Любители" in text
    assert "Идут: 3/10" in text
    assert "Взять воду" in text
    assert kb == [
        [Button("✅ Пойду", "join:7"), Button("❌ Не пойду", "decline:7")],
        [Button("👥 Участники (3)", "who:7")],
        [Button("🚲 Инвентарь", "inv:7")],
    ]


def test_card_without_limit_shows_plain_count():
    text, _ = render_card(card(capacity=None, going_count=4), MSK)
    assert "Идут: 4" in text and "/" not in text.split("Идут:")[1].split("\n")[0]


def test_unpublished_card_shows_opening_time_instead_of_join_buttons():
    text, kb = render_card(card(published=False), MSK)
    assert kb == [[Button("🕒 Откроется 19.10 19:00", "noop")]]
    assert "Идут" not in text


def test_cancelled_card_has_marker_and_no_buttons():
    text, kb = render_card(card(status="cancelled"), MSK)
    assert text.startswith("❌ Отменена")
    assert kb == []


def test_done_card_has_marker_and_no_buttons():
    text, kb = render_card(card(status="done"), MSK)
    assert text.startswith("🏁 Прошла")
    assert kb == []


def test_participants_text():
    assert render_participants(["Анна", "Борис"], ["Вера"]) == (
        "Идут (2):\n1. Анна\n2. Борис\n\nОтказались (1):\n– Вера"
    )
    assert render_participants([], []) == "Идут (0)"


def test_fit_alert_trims_long_text_and_keeps_short():
    assert fit_alert("коротко") == "коротко"
    long = render_participants([f"Участник {i}" for i in range(60)], [])
    trimmed = fit_alert(long)
    assert len(trimmed) <= 190 and trimmed.endswith("…")
