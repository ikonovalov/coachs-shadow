from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime


class ChargeKind:
    SESSION = "session"
    SURCHARGE = "surcharge"
    INVENTORY = "inventory"


class ChargeStatus:
    PENDING = "pending"  # долг
    COVERED = "covered"  # списано с абонемента
    PAID = "paid"
    VOID = "void"  # не засчитано


class PriceRule(Base):
    """Цены по умолчанию для типа тренировки (kind). None — не задано."""

    __tablename__ = "price_rules"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    single_price: Mapped[int | None] = mapped_column(Integer)
    surcharge_per_person: Mapped[int | None] = mapped_column(Integer)


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    sessions: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    valid_months: Mapped[int | None] = mapped_column(Integer)  # None — бессрочный


class SubscriptionPlanSeries(Base):
    __tablename__ = "subscription_plan_series"

    plan_id: Mapped[int] = mapped_column(ForeignKey("subscription_plans.id"), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("training_series.id"), primary_key=True)


class Subscription(Base):
    """Выданный абонемент. Название, цена и набор серий скопированы из тарифа при выдаче."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    sessions_total: Mapped[int] = mapped_column(Integer)
    sessions_left: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # None — бессрочный
    price: Mapped[int] = mapped_column(Integer)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paid_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    issued_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SubscriptionSeries(Base):
    __tablename__ = "subscription_series"

    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("training_series.id"), primary_key=True)


class Charge(Base):
    __tablename__ = "charges"
    __table_args__ = (UniqueConstraint("training_id", "user_id", "kind", "ref_id", name="uq_charge_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    ref_id: Mapped[int] = mapped_column(Integer, default=0)  # inventory: id заказа; иначе 0
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=ChargeStatus.PENDING, index=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"))
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    paid_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    refund_due: Mapped[bool] = mapped_column(Boolean, default=False)
    refunded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Attendance(Base):
    """Явная отметка посещения. Нет строки — считается, что записавшийся («Пойду») пришёл."""

    __tablename__ = "attendance"

    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), primary_key=True)
    present: Mapped[bool] = mapped_column(Boolean)
    marked_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    marked_at: Mapped[datetime] = mapped_column(UTCDateTime)
