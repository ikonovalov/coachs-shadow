from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def occurrences(
    *,
    weekdays: set[int],
    start_date: date,
    start_time: time,
    end_date: date | None,
    tz: ZoneInfo,
    window_start: datetime,
    window_end: datetime,
) -> list[datetime]:
    """UTC start times inside [window_start, window_end). Wall-clock time is evaluated in `tz`
    so a DST change does not shift the training."""
    first = max(start_date, window_start.astimezone(tz).date())
    last = window_end.astimezone(tz).date()
    if end_date is not None:
        last = min(last, end_date)
    result: list[datetime] = []
    day = first
    while day <= last:
        hit = (day == start_date) if not weekdays else (day.isoweekday() in weekdays)
        if hit:
            local = datetime.combine(day, start_time, tzinfo=tz)
            start = local.astimezone(timezone.utc)
            if window_start <= start < window_end:
                result.append(start)
        day += timedelta(days=1)
    return result
