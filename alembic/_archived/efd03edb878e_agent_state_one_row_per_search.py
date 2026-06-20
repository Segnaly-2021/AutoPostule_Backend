"""agent_state_one_row_per_search

Revision ID: efd03edb878e
Revises: ca10269f2d24
Create Date: 2026-05-08 19:34:52.916066

Changes agent_states from one-row-per-user to one-row-per-search.
- Drops UNIQUE constraint on user_id
- Makes search_id NOT NULL, UNIQUE, with FK to job_searches
- Adds cascade delete from job_searches

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'efd03edb878e'
down_revision: Union[str, Sequence[str], None] = 'ca10269f2d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Cleanest path: drop and recreate. Existing rows are ephemeral kill-switch
    # state with no historical value — losing them is harmless.
    op.drop_table("agent_states")

    op.create_table(
        "agent_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "search_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_searches.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "is_shutdown",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_agent_states_user_id", "agent_states", ["user_id"])
    op.create_index("ix_agent_states_search_id", "agent_states", ["search_id"])


def downgrade() -> None:
    # Restore the previous shape (id PK, unique user_id, nullable search_id)
    op.drop_index("ix_agent_states_search_id", table_name="agent_states")
    op.drop_index("ix_agent_states_user_id", table_name="agent_states")
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
    op.create_index("ix_agent_states_user_id", "agent_states", ["user_id"])
    op.create_index("ix_agent_states_search_id", "agent_states", ["search_id"])