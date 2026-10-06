from datetime import datetime

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base, UTCDateTime


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_jobs_pending", "done_at", "run_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    run_at: Mapped[datetime] = mapped_column(UTCDateTime)
    training_id: Mapped[int | None] = mapped_column(ForeignKey("trainings.id"), index=True)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
