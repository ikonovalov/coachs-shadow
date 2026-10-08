import pytest

from bot.core.errors import InvalidAction
from bot.core.money import format_money, format_range, parse_money, surcharge_estimate, surcharge_share


def test_format_money():
    assert format_money(70000) == "700 ₽"
    assert format_money(70050) == "700,50 ₽"
    assert format_money(0) == "0 ₽"
    assert format_money(-150) == "-1,50 ₽"


def test_format_range():
    assert format_range(60000, 75000) == "600–750 ₽"
    assert format_range(75000, 75000) == "750 ₽"
    assert format_range(60050, 75000) == "600,50–750 ₽"


@pytest.mark.parametrize("text,expected", [("700", 70000), ("700,5", 70050), (" 1 200 ", 120000), ("0", 0), ("12.34", 1234)])
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "-5", "1.005", "2000000", "NaN", "inf"])
def test_parse_money_rejects_garbage(text):
    with pytest.raises(InvalidAction):
        parse_money(text)


def test_surcharge_share_includes_the_trainers_part():
    assert surcharge_share(50000, 1) == 100000
    assert surcharge_share(50000, 4) == 62500
    assert surcharge_share(50000, 3) == 66667  # 200000 / 3, округление вверх
    assert surcharge_share(50000, 0) == 100000  # без участников считаем как с одним


def test_surcharge_estimate_runs_from_capacity_to_current_count():
    assert surcharge_estimate(50000, 3, 5) == (60000, 66667)
    assert surcharge_estimate(50000, 3, None) == (66667, 66667)
    assert surcharge_estimate(50000, 5, 5) == (60000, 60000)
