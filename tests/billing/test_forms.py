import pytest

from bot.billing.forms import PlanDraft, parse_plan_line, parse_prices_line
from bot.billing.queries import Prices
from bot.core.errors import InvalidAction


def test_plan_line_with_all_fields():
    assert parse_plan_line("Утро+вечер; 8; 5500; 2") == PlanDraft("Утро+вечер", 8, 550000, 2)


def test_plan_line_defaults_to_one_month_and_zero_means_unlimited():
    assert parse_plan_line("Месяц; 4; 3000").valid_months == 1
    assert parse_plan_line("Вечный; 10; 6000; 0").valid_months is None
    assert parse_plan_line("Вечный; 10; 6000; -").valid_months is None


@pytest.mark.parametrize("text", ["", "только название", "А; 4", "А; х; 100", "А; 4; х", "А; 4; 100; х", "А; 4; 100; 1; 2", "А; 4,5; 100"])
def test_plan_line_rejects_bad_input_with_a_hint(text):
    with pytest.raises(InvalidAction) as exc:
        parse_plan_line(text)
    assert "Название" in exc.value.message or "сумму" in exc.value.message or "число" in exc.value.message


def test_prices_line():
    assert parse_prices_line("700; 500") == Prices(70000, 50000)
    assert parse_prices_line("700") == Prices(70000, None)
    assert parse_prices_line("-; 0") == Prices(None, 0)
    assert parse_prices_line("700,50;-") == Prices(70050, None)


@pytest.mark.parametrize("text", ["", "а; б", "1; 2; 3", "-5"])
def test_prices_line_rejects_bad_input(text):
    with pytest.raises(InvalidAction):
        parse_prices_line(text)
