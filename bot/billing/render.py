from zoneinfo import ZoneInfo

from bot.billing.models import Charge, ChargeKind, ChargeStatus
from bot.billing.views import MyBilling
from bot.core.money import format_money
from bot.publishing.render import KIND_NAMES
from bot.schedule.models import Training

SUFFIX = {ChargeKind.SURCHARGE: " (доплата)", ChargeKind.INVENTORY: " (инвентарь)"}


def charge_label(charge: Charge, training: Training, tz: ZoneInfo) -> str:
    day = training.starts_at.astimezone(tz)
    return f"{day:%d.%m} · {KIND_NAMES.get(training.kind, training.kind)}{SUFFIX.get(charge.kind, '')}"


def render_new_charges(title: str, charges: list[Charge], sub_names: dict[int, str]) -> str:
    lines = [f"💳 {title}"]
    for charge in charges:
        if charge.kind == ChargeKind.SESSION and charge.status == ChargeStatus.COVERED:
            lines.append(f"✅ Списано с абонемента «{sub_names.get(charge.subscription_id, '—')}»")
        elif charge.kind == ChargeKind.SESSION:
            lines.append(f"К оплате: {format_money(charge.amount)}")
        elif charge.kind == ChargeKind.SURCHARGE:
            lines.append(f"➕ Доплата: {format_money(charge.amount)}")
        else:
            lines.append(f"🚲 Аренда инвентаря: {format_money(charge.amount)}")
    return "\n".join(lines)


def _history_line(charge: Charge, training: Training, tz: ZoneInfo) -> str:
    label = charge_label(charge, training, tz)
    if charge.status == ChargeStatus.COVERED:
        return f"• {label} — с абонемента"
    if charge.status == ChargeStatus.PAID:
        return f"• {label} — оплачено {format_money(charge.amount)}"
    return f"• {label} — не засчитано"


def render_my_billing(view: MyBilling, tz: ZoneInfo) -> str:
    lines = ["💳 Оплата", "", "Абонементы:"]
    if view.subscriptions:
        for sub in view.subscriptions:
            until = f"до {sub.expires_at.astimezone(tz):%d.%m.%Y}" if sub.expires_at else "бессрочный"
            lines.append(f"• «{sub.name}» — осталось {sub.sessions_left} из {sub.sessions_total}, {until}")
    else:
        lines.append("Действующих абонементов нет")
    lines += ["", "К оплате:"]
    if view.debts.total > 0:
        lines += [f"• {charge_label(c, t, tz)} — {format_money(c.amount)}" for c, t in view.debts.charges]
        lines += [f"• Абонемент «{s.name}» — {format_money(s.price)}" for s in view.debts.subscriptions]
        lines.append(f"Итого: {format_money(view.debts.total)}")
    else:
        lines.append("Задолженности нет")
    if view.history:
        lines += ["", "Последние занятия:"]
        lines += [_history_line(c, t, tz) for c, t in view.history]
    return "\n".join(lines)
