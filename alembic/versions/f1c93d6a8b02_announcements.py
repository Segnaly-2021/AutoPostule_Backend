"""announcements + announcement_views for the in-app release modal

Two tables so a release note can be published from the admin screen with no
deploy, and shown to each user exactly once.

announcements is bilingual by column rather than by JSON blob: the app is FR/EN
everywhere, and a typed column is what the admin form validates against.
is_published and the starts_at/ends_at window are separate controls -- the first
is the author's switch so a draft stays invisible, the second is the schedule --
and both are ANDed at read time.

announcement_views records dismissals server-side rather than in localStorage,
because a dismissal has to follow the user across devices. Its UNIQUE makes a
double-click, or a dismiss racing a reload, a no-op instead of a duplicate row.

The partial index on (is_published, starts_at) serves the only hot query in the
feature: every authenticated page load asks for the active announcements.

Revision ID: f1c93d6a8b02
Revises: e5b82d1a4f37
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1c93d6a8b02"
down_revision: Union[str, Sequence[str], None] = "e5b82d1a4f37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column(
            "id", sa.Uuid(), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title_fr", sa.String(length=160), nullable=False),
        sa.Column("title_en", sa.String(length=160), nullable=False),
        sa.Column("body_fr", sa.Text(), nullable=False),
        sa.Column("body_en", sa.Text(), nullable=False),
        # Nullable together with cta_url: a notice does not have to lead anywhere.
        sa.Column("cta_label_fr", sa.String(length=80), nullable=True),
        sa.Column("cta_label_en", sa.String(length=80), nullable=True),
        sa.Column("cta_url", sa.String(length=500), nullable=True),
        sa.Column("icon", sa.String(length=16), nullable=True),
        sa.Column(
            "is_published", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "starts_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        # Null means open-ended, rather than a far-future sentinel that would
        # read as a real date in the admin list and eventually arrive.
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Partial: the read path only ever asks for published rows, and drafts should
    # not pay for the index or bloat it.
    op.create_index(
        "ix_announcements_live",
        "announcements",
        ["starts_at"],
        postgresql_where=sa.text("is_published"),
    )

    op.create_table(
        "announcement_views",
        sa.Column(
            "id", sa.Uuid(), nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("announcement_id", sa.Uuid(), nullable=False),
        sa.Column(
            "dismissed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["announcement_id"], ["announcements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "announcement_id",
            name="uq_announcement_view_user_announcement",
        ),
    )
    op.create_index(
        "ix_announcement_views_user_id", "announcement_views", ["user_id"]
    )
    op.create_index(
        "ix_announcement_views_announcement_id",
        "announcement_views",
        ["announcement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_announcement_views_announcement_id", table_name="announcement_views"
    )
    op.drop_index("ix_announcement_views_user_id", table_name="announcement_views")
    op.drop_table("announcement_views")
    op.drop_index("ix_announcements_live", table_name="announcements")
    op.drop_table("announcements")
