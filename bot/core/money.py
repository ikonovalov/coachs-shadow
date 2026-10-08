"""Деньги хранятся в копейках (int); ввод и вывод — в рублях."""
from decimal import Decimal, InvalidOperation

from bot.core.errors import InvalidAction

MAX_RUBLES = 1_000_000
MONEY_HINT = "Введите сумму в рублях, например 700 или 700,50"


def _amount(kopecks: int) -> str:
    sign = "-" if kopecks < 0 else ""
    rub, kop = divmod(abs(kopecks), 100)
    return f"{sign}{rub}" if kop == 0 else f"{sign}{rub},{kop:02d}"


def format_money(kopecks: int) -> str:
    return f"{_amount(kopecks)} ₽"


def format_range(low: int, high: int) -> str:
    return format_money(low) if low == high else f"{_amount(low)}–{format_money(high)}"


def parse_money(text: str) -> int:
    try:
        value = Decimal(text.strip().replace(",", ".").replace(" ", ""))
    except InvalidOperation:
        raise InvalidAction(MONEY_HINT) from None
    if not value.is_finite() or value < 0 or value > MAX_RUBLES:
        raise InvalidAction(MONEY_HINT)
    kopecks = value * 100
    if kopecks != kopecks.to_integral_value():
        raise InvalidAction(MONEY_HINT)
    return int(kopecks)


def surcharge_share(tariff: int, attendees: int) -> int:
    """Доля доплаты на одного пришедшего: тариф платят все, включая тренера, а его часть делят ученики."""
    n = max(attendees, 1)
    return -(-tariff * (n + 1) // n)  # деление с округлением вверх до копейки


def surcharge_estimate(tariff: int, going: int, capacity: int | None) -> tuple[int, int]:
    """Ориентир (меньше, больше): с полной группой и с текущим числом записавшихся."""
    high = surcharge_share(tariff, going)
    low = surcharge_share(tariff, capacity) if capacity and capacity > going else high
    return low, high
