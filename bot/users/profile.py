import re

from bot.core.errors import InvalidAction
from bot.users.models import User

CLEAT_TYPES = ("none", "spd", "look", "spd_sl")
CLEAT_LABELS = {"none": "без шипов (плоские педали)", "spd": "SPD", "look": "Look", "spd_sl": "SPD-SL"}
HELMET_SIZES = ("S", "M", "L")
_CYRILLIC_HELMET = {"с": "S", "м": "M", "л": "L"}


def parse_height(text: str) -> int:
    match = re.fullmatch(r"(\d{2,3})(?:\s*см)?", text.strip().lower())
    if match and 100 <= int(match[1]) <= 230:
        return int(match[1])
    raise InvalidAction("Введите рост в сантиметрах, например 175")


def parse_foot(text: str) -> int:
    value = text.strip()
    if re.fullmatch(r"[0-9]{2}", value) and 30 <= int(value) <= 52:  # не isdigit(): «⁴²» — digit, но int() падает
        return int(value)
    raise InvalidAction("Введите размер ноги (EU), например 42")


def parse_helmet(text: str) -> str:
    value = text.strip().lower()
    value = _CYRILLIC_HELMET.get(value, value.upper())
    if value in HELMET_SIZES:
        return value
    raise InvalidAction("Размер шлема: S, M или L")


def parse_cleat(text: str) -> str:
    value = text.strip().lower()
    if value in CLEAT_TYPES:
        return value
    raise InvalidAction("Тип шипов: none, spd, look или spd_sl")


PROFILE_FIELDS = {
    "height_cm": parse_height,
    "foot_eu": parse_foot,
    "helmet_size": parse_helmet,
    "cleat_type": parse_cleat,
}
PROFILE_PROMPTS = {
    "height_cm": "Ваш рост в сантиметрах, например 175:",
    "foot_eu": "Размер ноги (EU), например 42:",
}


def render_profile(user: User) -> str:
    def show(value, fmt=str):
        return fmt(value) if value is not None else "не указан"

    return (
        f"Профиль: {user.name}\n"
        f"Рост: {show(user.height_cm, lambda v: f'{v} см')}\n"
        f"Размер ноги: {show(user.foot_eu)}\n"
        f"Размер шлема: {show(user.helmet_size)}\n"
        f"Шипы своих туфель: {show(user.cleat_type, lambda v: CLEAT_LABELS[v])}"
    )
