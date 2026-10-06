from datetime import date, datetime, time, timedelta

from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, String, Table, Text, Time, Index, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.core.db import Base, UTCDateTime


class TrainingKind:
    INTERVALS = "intervals"
    ENDURANCE = "endurance"
    TECHNIQUE = "technique"
    ALL = (INTERVALS, ENDURANCE, TECHNIQUE)


class TrainingStatus:
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    DONE = "done"


class CancelReason:
    USER = "user"
    SERIES_EDIT = "series_edit"


class Level(Base):
    __tablename__ = "levels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(200))
    level_id: Mapped[int] = mapped_column(ForeignKey("levels.id"))  # not unique: several chats per level


series_level = Table(
    "series_levels",
    Base.metadata,
    Column("series_id", ForeignKey("training_series.id"), primary_key=True),
    Column("level_id", ForeignKey("levels.id"), primary_key=True),
)

training_level = Table(
    "training_levels",
    Base.metadata,
    Column("training_id", ForeignKey("trainings.id"), primary_key=True),
    Column("level_id", ForeignKey("levels.id"), primary_key=True),
)


class TrainingSeries(Base):
    """Template. One-off training = series with empty `weekdays` that fires on `start_date`."""

    __tablename__ = "training_series"

    id: Mapped[int] = mapped_column(primary_key=True)
    trainer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    place: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    capacity: Mapped[int | None] = mapped_column(Integer)
    weekdays: Mapped[str] = mapped_column(String(20), default="")  # ISO weekdays "2,4"; "" = one-off
    start_date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)  # local club time
    end_date: Mapped[date | None] = mapped_column(Date)
    duration_min: Mapped[int] = mapped_column(Integer)
    levels: Mapped[list[Level]] = relationship(secondary=series_level, lazy="selectin")


class Training(Base):
    """Snapshot of the series fields, so editing a series never rewrites existing trainings."""

    __tablename__ = "trainings"
    __table_args__ = (
        Index(
            "uq_training_series_start",
            "series_id",
            "starts_at",
            unique=True,
            sqlite_where=text("COALESCE(cancel_reason, '') <> 'series_edit'"),
            postgresql_where=text("COALESCE(cancel_reason, '') <> 'series_edit'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("training_series.id"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    duration_min: Mapped[int] = mapped_column(Integer)
    trainer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    place: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text, default="")
    capacity: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default=TrainingStatus.SCHEDULED, index=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(16))
    levels: Mapped[list[Level]] = relationship(secondary=training_level, lazy="selectin")

    @property
    def ends_at(self) -> datetime:
        return self.starts_at + timedelta(minutes=self.duration_min)
