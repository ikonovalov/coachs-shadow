from dataclasses import dataclass

from bot.billing.queries import Prices
from bot.core.errors import InvalidAction
from bot.core.money import MONEY_HINT, parse_money

PLAN_FORMAT = (
    "Одной строкой через «;»: Название; занятий; цена; срок в месяцах.\n"
    "Срок необязателен (по умолчанию 1), 0 — бессрочный.\n"
    "Пример: Утро+вечер; 8; 5500; 2"
)
PRICE_FORMAT = (
    "Одной строкой через «;»: разовая цена; доплата на человека (₽).\n"
    "«-» — не задано (берётся значение типа), 0 — бесплатно / без доплаты.\n"
    "Пример: 700; 500"
)


@dataclass(frozen=True)
class PlanDraft:
    name: str
    sessions: int
    price: int  # копейки
    valid_months: int | None  # None — бессрочный


def _int(text: str, what: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise InvalidAction(f"{what}: введите целое число.\n{PLAN_FORMAT}") from None


def parse_plan_line(text: str) -> PlanDraft:
    parts = [p.strip() for p in text.split(";")]
    if len(parts) not in (3, 4) or not parts[0]:
        raise InvalidAction(f"Название; занятий; цена; срок.\n{PLAN_FORMAT}")
    months: int | None = 1
    if len(parts) == 4:
        months = None if parts[3] in ("0", "-") else _int(parts[3], "Срок")
    return PlanDraft(parts[0], _int(parts[1], "Занятий"), parse_money(parts[2]), months)


def parse_prices_line(text: str) -> Prices:
    parts = [p.strip() for p in text.split(";")]
    if len(parts) not in (1, 2) or not parts[0]:
        raise InvalidAction(f"Введите сумму.\n{PRICE_FORMAT}")

    def one(value: str) -> int | None:
        return None if value == "-" else parse_money(value)

    try:
        return Prices(one(parts[0]), one(parts[1]) if len(parts) == 2 else None)
    except InvalidAction as exc:
        raise InvalidAction(f"{exc.message}\n{PRICE_FORMAT}") from None
