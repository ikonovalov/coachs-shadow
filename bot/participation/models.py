from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime


class ParticipationStatus:
    GOING = "going"
    DECLINED = "declined"


class Participation(Base):
    __tablename__ = "participations"

    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    added_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
