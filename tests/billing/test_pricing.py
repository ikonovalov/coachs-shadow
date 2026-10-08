from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from bot.billing.pricing import add_months, subscription_expiry

MSK = ZoneInfo("Europe/Moscow")


def test_add_months_clamps_to_month_length():
    assert add_months(date(2026, 10, 8), 1) == date(2026, 11, 8)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_expiry_is_end_of_local_day_in_utc():
    now = datetime(2026, 10, 8, 20, 30, tzinfo=timezone.utc)  # 23:30 по Москве, 8 октября
    assert subscription_expiry(now, 1, MSK) == datetime(2026, 11, 8, 20, 59, 59, tzinfo=timezone.utc)


def test_expiry_uses_local_date_near_midnight():
    now = datetime(2026, 10, 8, 22, 30, tzinfo=timezone.utc)  # уже 9 октября в Москве
    assert subscription_expiry(now, 1, MSK).astimezone(MSK).date() == date(2026, 11, 9)


def test_no_months_means_no_expiry():
    assert subscription_expiry(datetime(2026, 10, 8, tzinfo=timezone.utc), None, MSK) is None
