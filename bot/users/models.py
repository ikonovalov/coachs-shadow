from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime, utcnow
from bot.core.permissions import Role


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default=Role.MEMBER)
    height_cm: Mapped[int | None] = mapped_column(Integer)
    foot_eu: Mapped[int | None] = mapped_column(Integer)
    helmet_size: Mapped[str | None] = mapped_column(String(2))  # S/M/L
    cleat_type: Mapped[str | None] = mapped_column(String(16))  # none/spd/look/spd_sl
    started_bot: Mapped[bool] = mapped_column(Boolean, default=False)  # may we DM this user?
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChatTrainer(Base):
    __tablename__ = "chat_trainers"
    __table_args__ = (
        Index(
            "uq_chat_one_lead",
            "chat_id",
            unique=True,
            sqlite_where=text("is_lead = 1"),
            postgresql_where=text("is_lead"),
        ),
    )

    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), primary_key=True)
    is_lead: Mapped[bool] = mapped_column(Boolean, default=False)
