from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.core.messenger import Button, Keyboard

KIND_NAMES = {"road": "Шоссе", "track": "Трек", "coffeeride": "Коферайд", "endurance": "Длительная"}
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
ALERT_LIMIT = 190  # Telegram callback alerts are capped at 200 characters


@dataclass(frozen=True)
class CardData:
    training_id: int
    status: str
    starts_at: datetime
    duration_min: int
    kind: str
    place: str
    trainer_name: str
    level_names: list[str]
    capacity: int | None
    going_count: int
    description: str
    published: bool
    opens_at: datetime


def format_when(starts_at: datetime, duration_min: int, tz: ZoneInfo) -> str:
    start = starts_at.astimezone(tz)
    end = (starts_at + timedelta(minutes=duration_min)).astimezone(tz)
    return f"{WEEKDAYS[start.weekday()]}, {start:%d.%m} {start:%H:%M}–{end:%H:%M}"


def render_card(card: CardData, tz: ZoneInfo) -> tuple[str, Keyboard]:
    lines: list[str] = []
    if card.status == "cancelled":
        lines.append("❌ Отменена")
    elif card.status == "done":
        lines.append("🏁 Прошла")
    lines.append(f"🚴 {KIND_NAMES.get(card.kind, card.kind)} · {format_when(card.starts_at, card.duration_min, tz)}")
    lines.append(f"📍 {card.place}")
    lines.append(f"👤 Тренер: {card.trainer_name}")
    lines.append(f"🎚 Уровни: {', '.join(card.level_names)}")
    if card.published:
        going = f"{card.going_count}/{card.capacity}" if card.capacity else str(card.going_count)
        lines.append(f"✅ Идут: {going}")
    if card.description:
        lines.append("")
        lines.append(card.description)

    keyboard: Keyboard = []
    if card.status == "scheduled":
        if card.published:
            tid = card.training_id
            keyboard = [
                [Button("✅ Пойду", f"join:{tid}"), Button("❌ Не пойду", f"decline:{tid}")],
                [Button("👥 Участники", f"who:{tid}")],
                [Button("🚲 Инвентарь", f"inv:{tid}")],
            ]
        else:
            opens = card.opens_at.astimezone(tz)
            keyboard = [[Button(f"🕒 Откроется {opens:%d.%m %H:%M}", "noop")]]
    return "\n".join(lines), keyboard


def render_participants(going: list[str], declined: list[str]) -> str:
    parts = [f"Идут ({len(going)})" + (":" if going else "")]
    parts += [f"{i}. {name}" for i, name in enumerate(going, 1)]
    text = "\n".join(parts)
    if declined:
        text += f"\n\nОтказались ({len(declined)}):\n" + "\n".join(f"– {name}" for name in declined)
    return text


def fit_alert(text: str, limit: int = ALERT_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
