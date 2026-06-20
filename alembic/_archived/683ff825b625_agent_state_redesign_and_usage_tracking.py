"""agent_state_redesign_and_usage_tracking

Revision ID: 683ff825b625
Revises: 93062db72aae
Create Date: 2026-05-07 13:46:35.033255

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '683ff825b625'
down_revision: Union[str, Sequence[str], None] = '93062db72aae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────────
    # 1. agent_states: redesign
    # ─────────────────────────────────────────────────────────────────
    # The cleanest way is to drop and recreate. Existing rows are
    # ephemeral kill-switch state — losing them is harmless (nothing
    # is currently running anyway since the app is offline).
    op.drop_table("agent_states")

    op.create_table(
        "agent_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "search_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "is_shutdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_agent_states_user_id", "agent_states", ["user_id"]
    )
    op.create_index(
        "ix_agent_states_search_id", "agent_states", ["search_id"]
    )

    # ─────────────────────────────────────────────────────────────────
    # 2. agent_usages: new table
    # ─────────────────────────────────────────────────────────────────
    op.create_table(
        "agent_usages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "runs_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "user_id", "usage_date", name="uq_agent_usage_user_date"
        ),
    )
    op.create_index(
        "ix_agent_usages_user_id", "agent_usages", ["user_id"]
    )
    op.create_index(
        "ix_agent_usages_usage_date", "agent_usages", ["usage_date"]
    )

    # ─────────────────────────────────────────────────────────────────
    # 3. free_search_usages: new table
    # ─────────────────────────────────────────────────────────────────
    op.create_table(
        "free_search_usages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column(
            "searches_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.UniqueConstraint(
            "user_id", "usage_date", name="uq_free_search_usage_user_date"
        ),
    )
    op.create_index(
        "ix_free_search_usages_user_id", "free_search_usages", ["user_id"]
    )
    op.create_index(
        "ix_free_search_usages_usage_date",
        "free_search_usages",
        ["usage_date"],
    )


def downgrade() -> None:
    # Drop new tables
    op.drop_index("ix_free_search_usages_usage_date", table_name="free_search_usages")
    op.drop_index("ix_free_search_usages_user_id", table_name="free_search_usages")
    op.drop_table("free_search_usages")

    op.drop_index("ix_agent_usages_usage_date", table_name="agent_usages")
    op.drop_index("ix_agent_usages_user_id", table_name="agent_usages")
    op.drop_table("agent_usages")

    # Restore agent_states to old shape (user_id as PK, no id, no search_id)
    op.drop_index("ix_agent_states_search_id", table_name="agent_states")
    op.drop_index("ix_agent_states_user_id", table_name="agent_states")
    op.drop_table("agent_states")

    op.create_table(
        "agent_states",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "is_shutdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )