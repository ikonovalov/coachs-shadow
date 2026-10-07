from dataclasses import replace
from datetime import date, time

import pytest

from bot.core.errors import InvalidAction
from bot.schedule.forms import (
    apply_series_field, apply_training_field, build_series_input, parse_capacity, parse_date, parse_kind,
    parse_optional_date, parse_time, parse_weekdays, series_details, series_title, summarize_series,
)
from bot.schedule.service import SeriesInput, TrainingEdit

TODAY = date(2026, 10, 6)


def test_parse_date():
    assert parse_date("20.10", TODAY) == date(2026, 10, 20)
    assert parse_date("5.10", TODAY) == date(2027, 10, 5)  # уже прошла в этом году -> следующий
    assert parse_date("06.10", TODAY) == date(2026, 10, 6)  # сегодня подходит
    assert parse_date(" 20.10.2026 ", TODAY) == date(2026, 10, 20)


@pytest.mark.parametrize("bad", ["31.02", "32.01", "20-10", "завтра", "", "20.13", "1.1.26"])
def test_parse_date_rejects_garbage_with_a_hint(bad):
    with pytest.raises(InvalidAction, match="20.10"):
        parse_date(bad, TODAY)


def test_parse_optional_date():
    assert parse_optional_date("-", TODAY) is None
    assert parse_optional_date("нет", TODAY) is None
    assert parse_optional_date("31.12", TODAY) == date(2026, 12, 31)


def test_parse_time():
    assert parse_time("19:00") == time(19, 0)
    assert parse_time("9.05") == time(9, 5)


@pytest.mark.parametrize("bad", ["25:00", "19:60", "7", "вечером", ""])
def test_parse_time_rejects_garbage(bad):
    with pytest.raises(InvalidAction, match="19:00"):
        parse_time(bad)


def test_parse_weekdays():
    assert parse_weekdays("вт чт") == {2, 4}
    assert parse_weekdays("Пн, ср,ПТ") == {1, 3, 5}
    assert parse_weekdays("понедельник воскресенье") == {1, 7}


@pytest.mark.parametrize("bad", ["", "вторник и пятница через раз", "xx"])
def test_parse_weekdays_rejects_garbage(bad):
    with pytest.raises(InvalidAction, match="вт чт"):
        parse_weekdays(bad)


def test_parse_capacity():
    assert parse_capacity("12") == 12
    assert parse_capacity("-") is None and parse_capacity("нет") is None
    for bad in ("0", "-3", "много", "1.5"):
        with pytest.raises(InvalidAction):
            parse_capacity(bad)


def test_parse_kind():
    assert parse_kind("Шоссе") == "road"
    assert parse_kind("длительная") == "endurance"
    assert parse_kind("coffeeride") == "coffeeride"
    with pytest.raises(InvalidAction):
        parse_kind("йога")


def base():
    return SeriesInput(
        kind="road", place="Парк", level_ids=[1, 2], start_date=TODAY, start_time=time(19, 0),
        weekdays={2, 4}, capacity=10,
    )


def test_build_series_input_from_fsm_data():
    data = {
        "kind": "road", "place": "Парк", "level_ids": [1, 2], "weekdays": [2, 4],
        "start_date": "2026-10-06", "start_time": "19:00", "end_date": None, "capacity": 10, "description": "",
    }
    assert build_series_input(data) == base()
    one_off = {**data, "weekdays": [], "start_date": "2026-10-20"}
    assert build_series_input(one_off).weekdays == set()


def test_summary_mentions_all_decisions():
    text = summarize_series(base(), ["Новички", "Любители"])
    for part in ("Шоссе", "Парк", "Новички, Любители", "вт, чт", "19:00", "10"):
        assert part in text
    one_off = summarize_series(SeriesInput(
        kind="coffeeride", place="Зал", level_ids=[1], start_date=date(2026, 10, 20), start_time=time(8, 0),
    ), ["Новички"])
    assert "20.10.2026" in one_off and "без лимита" in one_off


def test_apply_series_field_changes_only_that_field():
    inp = base()
    assert apply_series_field(inp, "place", " Стадион ", TODAY).place == "Стадион"
    assert apply_series_field(inp, "weekdays", "пн", TODAY).weekdays == {1}
    assert apply_series_field(inp, "time", "7:30", TODAY).start_time == time(7, 30)
    assert apply_series_field(inp, "capacity", "-", TODAY).capacity is None
    assert apply_series_field(inp, "end_date", "31.12", TODAY).end_date == date(2026, 12, 31)
    assert apply_series_field(inp, "description", "-", TODAY).description == ""
    assert apply_series_field(inp, "kind", "коферайд", TODAY).kind == "coffeeride"
    assert apply_series_field(inp, "place", "Стадион", TODAY).capacity == 10
    with pytest.raises(InvalidAction):
        apply_series_field(inp, "level_ids", "1", TODAY)


def test_apply_training_field():
    assert apply_training_field("place", "Склон") == TrainingEdit(place="Склон")
    assert apply_training_field("description", "Дождь") == TrainingEdit(description="Дождь")
    assert apply_training_field("capacity", "4") == TrainingEdit(capacity=4)
    assert apply_training_field("capacity", "-") == TrainingEdit(clear_capacity=True)


@pytest.mark.parametrize("bad", ["²", "³", "١٢"])
def test_parse_capacity_rejects_non_ascii_digits(bad):
    with pytest.raises(InvalidAction):
        parse_capacity(bad)


@pytest.mark.parametrize("empty", ["", "   ", "-"])
def test_empty_place_is_rejected(empty):
    with pytest.raises(InvalidAction, match="Место"):
        apply_series_field(base(), "place", empty, TODAY)
    with pytest.raises(InvalidAction, match="Место"):
        apply_training_field("place", empty)


def test_series_title_for_weekly_and_long_place():
    inp = SeriesInput(
        kind="road", place="Парк", level_ids=[1], start_date=date(2026, 10, 6), start_time=time(19, 0),
        weekdays={4, 2},
    )
    assert series_title(inp) == "Вт/Чт 19:00 · Парк"
    long = replace(inp, place="х" * 100)
    assert len(series_title(long)) == 64


def test_series_details_adds_start_date():
    inp = SeriesInput(
        kind="road", place="Парк", level_ids=[1], start_date=date(2026, 10, 6), start_time=time(19, 0),
        weekdays={2}, end_date=date(2026, 12, 31),
    )
    text = series_details(inp, ["Новички"])
    assert "Уровни: Новички" in text and "до 31.12.2026" in text
    assert text.endswith("Начало: 06.10.2026")
