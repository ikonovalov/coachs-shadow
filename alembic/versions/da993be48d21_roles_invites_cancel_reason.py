"""roles invites cancel reason

Revision ID: da993be48d21
Revises: 64d92afd373f
Create Date: 2026-10-06 17:08:46.125290

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import bot.core.db


# revision identifiers, used by Alembic.
revision: str = 'da993be48d21'
down_revision: Union[str, Sequence[str], None] = '64d92afd373f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WHERE = "COALESCE(cancel_reason, '') <> 'series_edit'"


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger, sa.ForeignKey("users.telegram_id"), nullable=False),
        sa.Column("created_at", bot.core.db.UTCDateTime(), nullable=False),
        sa.Column("expires_at", bot.core.db.UTCDateTime(), nullable=False),
        sa.Column("used_by", sa.BigInteger, sa.ForeignKey("users.telegram_id"), nullable=True),
        sa.Column("used_at", bot.core.db.UTCDateTime(), nullable=True),
    )
    with op.batch_alter_table("trainings") as batch:
        batch.add_column(sa.Column("cancel_reason", sa.String(16), nullable=True))
        batch.drop_constraint("uq_training_series_start", type_="unique")
    op.create_index(
        "uq_training_series_start", "trainings", ["series_id", "starts_at"], unique=True,
        sqlite_where=sa.text(_WHERE), postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_training_series_start", table_name="trainings")
    with op.batch_alter_table("trainings") as batch:
        batch.create_unique_constraint("uq_training_series_start", ["series_id", "starts_at"])
        batch.drop_column("cancel_reason")
    op.drop_table("invites")
