import calendar
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo


def add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def subscription_expiry(now: datetime, months: int | None, tz: ZoneInfo) -> datetime | None:
    """Конец локального дня через `months` календарных месяцев, в UTC. None — бессрочный."""
    if months is None:
        return None
    last_day = add_months(now.astimezone(tz).date(), months)
    return datetime.combine(last_day, time(23, 59, 59), tzinfo=tz).astimezone(timezone.utc)
