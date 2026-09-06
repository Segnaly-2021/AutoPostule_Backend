"""add job_offers.submitted_at for per-cycle volume billing

Pricing v2 bills a volume of applications per billing cycle (180 BASIC /
300 PREMIUM), so it needs to know when an application was SENT.

`application_date` cannot answer that. It is stamped when the offer is first
saved, while the offer is still FOUND (job_offer_repo_db.save), and it is listed
in IMMUTABLE_FIELDS so the later re-stamp attempt never lands. It is also load
bearing elsewhere -- 30-day dedup (get_recent_application_hashes) and follow-up
scheduling both read it as find-time -- so repurposing it would silently change
those. Hence a separate column.

Backfill: existing SUBMITTED rows get application_date. It is the wrong instant
(find-time, typically minutes to hours early) but it is the only evidence we
have, and it keeps historical rows inside the right billing cycle in all but the
midnight-boundary case. New rows are stamped correctly from here on.

The partial index is on (user_id, submitted_at) because the billing query is
always per user and windowed -- unlike ix_job_offers_submitted_app_date, which is
on application_date alone and cannot serve it.

Revision ID: d4a71c9e0b25
Revises: c3f88a17be40
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a71c9e0b25"
down_revision: Union[str, Sequence[str], None] = "c3f88a17be40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_offers",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Only SUBMITTED rows: a FOUND offer has not been sent and must stay NULL,
    # or it would count against the user's volume.
    op.execute(
        """
        UPDATE job_offers
           SET submitted_at = application_date
         WHERE status = 'SUBMITTED'
           AND submitted_at IS NULL
           AND application_date IS NOT NULL
        """
    )

    op.create_index(
        "ix_job_offers_user_submitted_at",
        "job_offers",
        ["user_id", "submitted_at"],
        postgresql_where=sa.text("submitted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_job_offers_user_submitted_at", table_name="job_offers")
    op.drop_column("job_offers", "submitted_at")
