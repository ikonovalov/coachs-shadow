from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bot.billing.models import Charge, ChargeKind, ChargeStatus, Subscription
from bot.billing.render import charge_label, render_new_charges
from bot.schedule.models import Training

MSK = ZoneInfo("Europe/Moscow")
WHEN = datetime(2026, 10, 14, 16, 0, tzinfo=timezone.utc)


def training():
    return Training(id=1, starts_at=WHEN, duration_min=90, kind="track")


def charge(kind, status, amount, sub=None):
    return Charge(id=1, user_id=1, training_id=1, kind=kind, amount=amount, status=status, subscription_id=sub)


def test_charge_label_has_date_kind_and_suffix():
    assert charge_label(charge(ChargeKind.SESSION, "pending", 1), training(), MSK) == "14.10 · Трек"
    assert charge_label(charge(ChargeKind.SURCHARGE, "pending", 1), training(), MSK) == "14.10 · Трек (доплата)"
    assert charge_label(charge(ChargeKind.INVENTORY, "pending", 1), training(), MSK) == "14.10 · Трек (инвентарь)"


def test_new_charges_message():
    text = render_new_charges(
        "Трек, вторник 14.10", [
            charge(ChargeKind.SESSION, ChargeStatus.COVERED, 70000, sub=5),
            charge(ChargeKind.SURCHARGE, ChargeStatus.PENDING, 66667),
            charge(ChargeKind.INVENTORY, ChargeStatus.PENDING, 30000),
        ], {5: "Утро+вечер"},
    )
    assert "Трек, вторник 14.10" in text
    assert "Списано с абонемента «Утро+вечер»" in text
    assert "Доплата: 666,67 ₽" in text and "Аренда инвентаря: 300 ₽" in text


def test_new_charges_pending_session():
    text = render_new_charges("Шоссе", [charge(ChargeKind.SESSION, ChargeStatus.PENDING, 70000)], {})
    assert "К оплате: 700 ₽" in text
