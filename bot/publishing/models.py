from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from bot.core.db import Base


class TrainingPost(Base):
    __tablename__ = "training_posts"

    training_id: Mapped[int] = mapped_column(ForeignKey("trainings.id"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id"), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
