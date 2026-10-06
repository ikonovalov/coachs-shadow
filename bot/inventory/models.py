from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime, utcnow


class ItemKind:
    BIKE = "bike"
    PEDALS = "pedals"
    SHOES = "shoes"
    HELMET = "helmet"
    ALL = (BIKE, PEDALS, SHOES, HELMET)


class OrderStatus:
    ACTIVE = "active"
    CANCELLED = "cancelled"


class InventoryItem(Base):
    """Конкретная единица парка («Велосипед №7»). Неиспользуемые видом колонки пусты."""

    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    label: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    frame: Mapped[str | None] = mapped_column(String(8))  # велосипед: размер рамы
    height_min: Mapped[int | None] = mapped_column(Integer)  # велосипед: диапазон роста, см
    height_max: Mapped[int | None] = mapped_column(Integer)
    shoe_eu: Mapped[int | None] = mapped_column(Integer)  # туфли
    cleat_type: Mapped[str | None] = mapped_column(String(16))  # туфли: none/spd/look/spd_sl
    pedal_type: Mapped[str | None] = mapped_column(String(16))  # педали: flat/spd/look/spd_sl
    helmet_size: Mapped[str | None] = mapped_column(String(2))  # шлем: S/M/L
    price_per_use: Mapped[int | None] = mapped_column(Integer)  # в копейках, под оплату


class InventoryOrder(Base):
    __tablename__ = "inventory_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=OrderStatus.ACTIVE)
    price: Mapped[int | None] = mapped_column(Integer)
    paid_status: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CleatCompat(Base):
    """Какие туфли (по типу шипов) подходят к каким педалям. Данные, а не код."""

    __tablename__ = "cleat_compat"

    cleat_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    pedal_type: Mapped[str] = mapped_column(String(16), primary_key=True)


DEFAULT_COMPAT = (("none", "flat"), ("spd", "spd"), ("look", "look"), ("spd_sl", "spd_sl"))


async def ensure_cleat_compat(session: AsyncSession) -> None:
    existing = {(r.cleat_type, r.pedal_type) for r in await session.scalars(select(CleatCompat))}
    for cleat, pedal in DEFAULT_COMPAT:
        if (cleat, pedal) not in existing:
            session.add(CleatCompat(cleat_type=cleat, pedal_type=pedal))
    await session.commit()
