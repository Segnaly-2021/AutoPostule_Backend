"""message_log + auth_users.marketing_opt_out for lifecycle messaging

Two things the first scheduled email job needs.

message_log records one row per lifecycle message actually delivered. The UNIQUE
on (user_id, kind) is the whole point: the daily job re-selects the same cohort
every run, so a retry, a backfill, or two overlapping executions would all
re-send without it. The constraint makes a double-send a database error rather
than an embarrassment.

marketing_opt_out gates the free-account reminder only. Transactional mail
(verification, password reset) ignores it, as it must -- a user cannot opt out of
being told their password was changed.

Both columns carry server defaults, unlike auth_users.created_at which is
Python-side only: a row inserted by a migration or by hand should still be
well-formed.

Revision ID: e5b82d1a4f37
Revises: d4a71c9e0b25
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5b82d1a4f37"
down_revision: Union[str, Sequence[str], None] = "d4a71c9e0b25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column(
            "marketing_opt_out",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    op.create_table(
        "message_log",
        sa.Column(
            "id", sa.Uuid(), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "NEW_CUSTOMER_CHECKIN", "FREE_ACCOUNT_REMINDER",
                name="messagekind", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "kind", name="uq_message_log_user_kind"),
    )
    op.create_index("ix_message_log_user_id", "message_log", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_message_log_user_id", table_name="message_log")
    op.drop_table("message_log")
    op.drop_column("auth_users", "marketing_opt_out")
