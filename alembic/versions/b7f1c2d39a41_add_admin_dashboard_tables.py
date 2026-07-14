"""admin dashboard: is_admin, page_views, credit_transactions

Revision ID: b7f1c2d39a41
Revises: a1b2c3d4e5f6
Create Date: 2026-07-14 00:00:00.000000

Everything the admin dashboard needs, in one revision:

  * auth_users.is_admin  — the admin flag, re-read from the DB on every admin request
                           rather than trusted from a JWT claim, so revoking it is instant.
  * page_views           — anonymous visitor tracking. Nothing here identifies a person:
                           no IP, no user agent, no cookie. user_id is filled only when the
                           caller happened to send a valid token, which is what powers the
                           signed-in vs anonymous split.
  * credit_transactions  — an append-only ledger of AI credit movements. Needed because
                           UserSubscription.replenish_credits() RESETS ai_credits_balance
                           every billing cycle, which destroys the consumption history.
                           Without this table, "credits consumed" is unanswerable beyond
                           the current period.

Deliberately NOT backfilled. There is no source of truth for historical credit
consumption, and fabricating rows with invented timestamps would corrupt every time series
built on top of the ledger. The dashboard reports `ledger_started_at` instead, so the gap
is explicit rather than silently misleading.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7f1c2d39a41'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Admin flag
    # ------------------------------------------------------------------
    # NOT NULL with a false server_default, so every existing row is a non-admin:
    # the privileged state must be granted explicitly, never inherited.
    op.add_column(
        "auth_users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    # ------------------------------------------------------------------
    # Visitor tracking
    # ------------------------------------------------------------------
    op.create_table(
        "page_views",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("visitor_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("referrer", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_page_views_created_at", "page_views", ["created_at"])
    op.create_index("ix_page_views_user_id", "page_views", ["user_id"])
    # (visitor_id, created_at) serves the sessions query, which walks each visitor's views
    # in time order to find the gaps between visits.
    op.create_index("ix_page_views_visitor_created", "page_views", ["visitor_id", "created_at"])

    # ------------------------------------------------------------------
    # AI credit ledger
    # ------------------------------------------------------------------
    op.create_table(
        "credit_transactions",
        # server_default is required, not cosmetic: CONSUME rows are inserted by an
        # INSERT ... SELECT inside a CTE in SubscriptionRepoDB.try_consume_credits. That
        # statement never passes through the ORM, so Postgres has to generate the id.
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),           # negative = consumed
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("CONSUME", "REPLENISH", name="credittxkind", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credit_transactions_created_at", "credit_transactions", ["created_at"])
    op.create_index("ix_credit_transactions_user_id", "credit_transactions", ["user_id"])
    op.create_index("ix_credit_tx_user_created", "credit_transactions", ["user_id", "created_at"])

    # ------------------------------------------------------------------
    # Indexes supporting the dashboard's aggregate queries
    # ------------------------------------------------------------------
    # Signup metrics read auth_users.created_at — the users table has no timestamp at all.
    op.create_index("ix_auth_users_created_at", "auth_users", ["created_at"])

    # Partial index for "applications sent". Only SUBMITTED rows are indexed, so it stays
    # small while serving the total and both month windows. status is VARCHAR here — the
    # enum is mapped with native_enum=False, which stores the member NAME.
    op.create_index(
        "ix_job_offers_submitted_app_date",
        "job_offers",
        ["application_date"],
        postgresql_where=sa.text("status = 'SUBMITTED'"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_offers_submitted_app_date", table_name="job_offers")
    op.drop_index("ix_auth_users_created_at", table_name="auth_users")

    op.drop_index("ix_credit_tx_user_created", table_name="credit_transactions")
    op.drop_index("ix_credit_transactions_user_id", table_name="credit_transactions")
    op.drop_index("ix_credit_transactions_created_at", table_name="credit_transactions")
    op.drop_table("credit_transactions")

    op.drop_index("ix_page_views_visitor_created", table_name="page_views")
    op.drop_index("ix_page_views_user_id", table_name="page_views")
    op.drop_index("ix_page_views_created_at", table_name="page_views")
    op.drop_table("page_views")

    op.drop_column("auth_users", "is_admin")
