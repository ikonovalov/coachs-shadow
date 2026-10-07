import re
from dataclasses import replace
from datetime import date, time

from bot.core.errors import InvalidAction
from bot.schedule.models import TrainingKind
from bot.schedule.service import SeriesInput, TrainingEdit

KIND_BY_NAME = {"интервалы": "intervals", "длительная": "endurance", "техника": "technique"}
KIND_LABELS = {v: k.capitalize() for k, v in KIND_BY_NAME.items()}
WEEKDAY_BY_PREFIX = {
    "пн": 1, "по": 1, "вт": 2, "ср": 3, "чт": 4, "че": 4, "пт": 5, "пя": 5,
    "сб": 6, "су": 6, "вс": 7, "во": 7,
}
WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_NO = {"-", "нет", "—"}

SERIES_FIELD_PROMPTS = {
    "kind": "Тип: интервалы, длительная или техника",
    "place": "Новое место",
    "description": "Новое описание (или «-», чтобы убрать)",
    "capacity": "Лимит мест (число) или «-» без лимита",
    "weekdays": "Дни недели, например: вт чт",
    "time": "Время начала, например: 19:00",
    "end_date": "Дата окончания, например 31.12, или «-» без окончания",
}
TRAINING_FIELD_PROMPTS = {
    "place": SERIES_FIELD_PROMPTS["place"],
    "description": SERIES_FIELD_PROMPTS["description"],
    "capacity": SERIES_FIELD_PROMPTS["capacity"],
}


def parse_date(text: str, today: date) -> date:
    hint = "Введите дату как 20.10 или 20.10.2026"
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", text.strip())
    if not match:
        raise InvalidAction(hint)
    day, month = int(match[1]), int(match[2])
    try:
        if match[3]:
            return date(int(match[3]), month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        raise InvalidAction(hint) from None


def parse_optional_date(text: str, today: date) -> date | None:
    return None if text.strip().lower() in _NO else parse_date(text, today)


def parse_time(text: str) -> time:
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", text.strip())
    if match and int(match[1]) < 24 and int(match[2]) < 60:
        return time(int(match[1]), int(match[2]))
    raise InvalidAction("Введите время как 19:00")


def parse_weekdays(text: str) -> set[int]:
    days: set[int] = set()
    for token in re.split(r"[,\s]+", text.strip().lower()):
        if not token:
            continue
        day = WEEKDAY_BY_PREFIX.get(token[:2])
        if day is None or len(token) > 11:
            raise InvalidAction("Введите дни недели, например: вт чт")
        days.add(day)
    if not days:
        raise InvalidAction("Введите дни недели, например: вт чт")
    return days


def parse_capacity(text: str) -> int | None:
    value = text.strip().lower()
    if value in _NO:
        return None
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise InvalidAction("Введите целое число больше нуля или «-» без лимита")
    return int(value)


def parse_kind(text: str) -> str:
    value = text.strip().lower()
    if value in KIND_BY_NAME:
        return KIND_BY_NAME[value]
    if value in TrainingKind.ALL:
        return value
    raise InvalidAction("Тип: интервалы, длительная или техника")


def parse_place(text: str) -> str:
    value = text.strip()
    if not value or value.lower() in _NO:
        raise InvalidAction("Место не может быть пустым")
    return value


def build_series_input(data: dict) -> SeriesInput:
    return SeriesInput(
        kind=data["kind"],
        place=data["place"],
        level_ids=list(data["level_ids"]),
        start_date=date.fromisoformat(data["start_date"]),
        start_time=time.fromisoformat(data["start_time"]),
        weekdays=set(data.get("weekdays", [])),
        end_date=date.fromisoformat(data["end_date"]) if data.get("end_date") else None,
        capacity=data.get("capacity"),
        description=data.get("description", ""),
    )


def summarize_series(inp: SeriesInput, level_names: list[str]) -> str:
    if inp.weekdays:
        days = ", ".join(WEEKDAY_SHORT[d - 1] for d in sorted(inp.weekdays))
        when = f"каждый {days} в {inp.start_time:%H:%M}"
        if inp.end_date:
            when += f" до {inp.end_date:%d.%m.%Y}"
    else:
        when = f"{inp.start_date:%d.%m.%Y} в {inp.start_time:%H:%M}"
    lines = [
        f"Тип: {KIND_LABELS.get(inp.kind, inp.kind)}",
        f"Место: {inp.place}",
        f"Уровни: {', '.join(level_names)}",
        f"Когда: {when}",
        f"Лимит мест: {inp.capacity if inp.capacity else 'без лимита'}",
    ]
    if inp.description:
        lines.append(f"Описание: {inp.description}")
    return "\n".join(lines)


def series_title(inp: SeriesInput) -> str:
    """Подпись кнопки серии: «Вт/Чт 19:00 · Парк» (до 64 символов)."""
    when = "/".join(WEEKDAY_SHORT[d - 1].capitalize() for d in sorted(inp.weekdays)) or f"{inp.start_date:%d.%m}"
    return f"{when} {inp.start_time:%H:%M} · {inp.place}"[:64]


def series_details(inp: SeriesInput, level_names: list[str]) -> str:
    return summarize_series(inp, level_names) + f"\nНачало: {inp.start_date:%d.%m.%Y}"


def apply_series_field(inp: SeriesInput, field: str, text: str, today: date) -> SeriesInput:
    if field == "place":
        return replace(inp, place=parse_place(text))
    if field == "description":
        return replace(inp, description="" if text.strip() in _NO else text.strip())
    if field == "capacity":
        return replace(inp, capacity=parse_capacity(text))
    if field == "weekdays":
        return replace(inp, weekdays=parse_weekdays(text))
    if field == "time":
        return replace(inp, start_time=parse_time(text))
    if field == "end_date":
        return replace(inp, end_date=parse_optional_date(text, today))
    if field == "kind":
        return replace(inp, kind=parse_kind(text))
    raise InvalidAction("Это поле нельзя изменить")


def apply_training_field(field: str, text: str) -> TrainingEdit:
    if field == "place":
        return TrainingEdit(place=parse_place(text))
    if field == "description":
        return TrainingEdit(description="" if text.strip() in _NO else text.strip())
    if field == "capacity":
        capacity = parse_capacity(text)
        return TrainingEdit(clear_capacity=True) if capacity is None else TrainingEdit(capacity=capacity)
    raise InvalidAction("Это поле нельзя изменить")
