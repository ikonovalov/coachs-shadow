from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from bot.schedule.recurrence import occurrences

UTC = timezone.utc
MSK = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_weekly_rule_in_club_timezone():
    got = occurrences(
        weekdays={2, 4}, start_date=date(2026, 10, 1), start_time=time(19, 0), end_date=None, tz=MSK,
        window_start=utc(2026, 10, 12), window_end=utc(2026, 10, 19),
    )
    assert got == [utc(2026, 10, 13, 16), utc(2026, 10, 15, 16)]  # Tue, Thu 19:00 MSK


def test_dst_change_keeps_local_wall_time():
    got = occurrences(
        weekdays={2}, start_date=date(2026, 10, 1), start_time=time(19, 0), end_date=None, tz=BERLIN,
        window_start=utc(2026, 10, 19), window_end=utc(2026, 10, 29),
    )
    # DST ends on 2026-10-25: 19:00 local is 17:00 UTC before, 18:00 UTC after
    assert got == [utc(2026, 10, 20, 17), utc(2026, 10, 27, 18)]


def test_start_date_and_end_date_bound_the_series():
    got = occurrences(
        weekdays={2}, start_date=date(2026, 10, 13), start_time=time(19, 0), end_date=date(2026, 10, 20), tz=MSK,
        window_start=utc(2026, 10, 1), window_end=utc(2026, 12, 1),
    )
    assert got == [utc(2026, 10, 13, 16), utc(2026, 10, 20, 16)]


def test_one_off_fires_once_on_start_date():
    got = occurrences(
        weekdays=set(), start_date=date(2026, 10, 20), start_time=time(19, 0), end_date=None, tz=MSK,
        window_start=utc(2026, 10, 1), window_end=utc(2026, 12, 1),
    )
    assert got == [utc(2026, 10, 20, 16)]


def test_occurrence_before_window_start_is_skipped():
    got = occurrences(
        weekdays=set(), start_date=date(2026, 10, 20), start_time=time(19, 0), end_date=None, tz=MSK,
        window_start=utc(2026, 10, 20, 17), window_end=utc(2026, 12, 1),
    )
    assert got == []
