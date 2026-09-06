"""fingerprint persona pool: drop unique user_id, add rotation columns

Turns user_fingerprints from "one frozen device per user" into a pool of device
personas that rotate.

The load-bearing change is dropping the UNIQUE constraint on user_id. That
constraint is what made a fingerprint permanent: with it, a user could only ever
have one row, and the repository's merge() on a freshly-minted entity id was an
insert conflict rather than an update. Uniqueness moves to (user_id, slot).

user_agent is dropped because it is now derived from platform + chrome_major
(see UserFingerprint.user_agent). The chrome major is parsed out of the stored UA
before the column goes, so existing personas keep the version they were using.

NOTE: this repo has two alembic heads (b7f1c2d39a41 and efd03edb878e) from a
pre-existing baseline that never retired the legacy chain. This revision attaches
to b7f1c2d39a41, the post-baseline chain. It does not merge the two — that needs
confirmation of what the database is actually stamped at.

Revision ID: c3f88a17be40
Revises: b7f1c2d39a41
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f88a17be40'
down_revision: Union[str, Sequence[str], None] = 'b7f1c2d39a41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- New columns, all with server defaults so existing rows stay valid ---
    op.add_column('user_fingerprints', sa.Column('slot', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('user_fingerprints', sa.Column('board', sa.String(length=50), nullable=True))
    op.add_column('user_fingerprints', sa.Column('session_count', sa.Integer(), nullable=False, server_default=sa.text('0')))
    op.add_column('user_fingerprints', sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column('user_fingerprints', sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_fingerprints', sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('user_fingerprints', sa.Column('chrome_major', sa.Integer(), nullable=False, server_default=sa.text('131')))
    op.add_column('user_fingerprints', sa.Column('screen_width', sa.Integer(), nullable=False, server_default=sa.text('1920')))
    op.add_column('user_fingerprints', sa.Column('screen_height', sa.Integer(), nullable=False, server_default=sa.text('1080')))
    op.add_column('user_fingerprints', sa.Column('device_memory', sa.Integer(), nullable=False, server_default=sa.text('8')))
    op.add_column('user_fingerprints', sa.Column('canvas_seed', sa.String(length=64), nullable=False, server_default=sa.text("''")))
    op.add_column('user_fingerprints', sa.Column('audio_seed', sa.String(length=64), nullable=False, server_default=sa.text("''")))

    # --- Backfill from what the old rows already carry ---

    # Chrome major out of the UA string, before user_agent is dropped.
    # substring(...) returns NULL on no match, so COALESCE holds the default.
    op.execute("""
        UPDATE user_fingerprints
        SET chrome_major = COALESCE(
            NULLIF(substring(user_agent from 'Chrome/([0-9]+)'), '')::int,
            131
        )
    """)

    # A screen at least as large as the window that was recorded inside it.
    # Snap to the nearest standard resolution at or above the viewport so the
    # pair stays plausible rather than exactly equal.
    op.execute("""
        UPDATE user_fingerprints
        SET screen_width = CASE
                WHEN viewport_width <= 1366 THEN 1366
                WHEN viewport_width <= 1600 THEN 1600
                WHEN viewport_width <= 1920 THEN 1920
                WHEN viewport_width <= 2560 THEN 2560
                ELSE 3840
            END,
            screen_height = CASE
                WHEN viewport_height <= 768 THEN 768
                WHEN viewport_height <= 900 THEN 900
                WHEN viewport_height <= 1080 THEN 1080
                WHEN viewport_height <= 1440 THEN 1440
                ELSE 2160
            END
    """)

    # RAM that matches the core count already stored.
    op.execute("""
        UPDATE user_fingerprints
        SET device_memory = CASE
                WHEN hardware_concurrency <= 4 THEN 8
                WHEN hardware_concurrency <= 8 THEN 8
                WHEN hardware_concurrency <= 12 THEN 16
                ELSE 32
            END
    """)

    # Seeds: derive from the row id so every persona starts with a distinct,
    # non-empty baseline. Runs override these in memory anyway.
    op.execute("""
        UPDATE user_fingerprints
        SET canvas_seed = substring(md5(id::text || 'canvas') from 1 for 16),
            audio_seed  = substring(md5(id::text || 'audio')  from 1 for 16)
    """)

    # --- The actual unlock: user_id stops being unique ---
    # The old index was created as a UNIQUE index (not a table constraint) by
    # revision 298a954f5091, so it is dropped and recreated non-unique.
    op.drop_index('ix_user_fingerprints_user_id', table_name='user_fingerprints')
    op.create_index('ix_user_fingerprints_user_id', 'user_fingerprints', ['user_id'], unique=False)
    op.create_index('ix_user_fingerprints_board', 'user_fingerprints', ['board'], unique=False)
    op.create_unique_constraint('uq_user_fingerprints_user_slot', 'user_fingerprints', ['user_id', 'slot'])

    # user_agent is now derived from platform + chrome_major.
    op.drop_column('user_fingerprints', 'user_agent')


def downgrade() -> None:
    """Downgrade schema.

    Lossy by nature: a user may hold several personas by now, and the restored
    UNIQUE(user_id) admits only one. Everything but the lowest slot is deleted.
    """
    op.add_column('user_fingerprints', sa.Column('user_agent', sa.String(length=500), nullable=True))

    op.execute("""
        UPDATE user_fingerprints
        SET user_agent = CASE
            WHEN platform = 'MacIntel' THEN
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                || '(KHTML, like Gecko) Chrome/' || chrome_major || '.0.0.0 Safari/537.36'
            ELSE
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                || '(KHTML, like Gecko) Chrome/' || chrome_major || '.0.0.0 Safari/537.36'
        END
    """)
    op.alter_column('user_fingerprints', 'user_agent', nullable=False)

    # Keep only the lowest slot per user so UNIQUE(user_id) can be restored.
    op.execute("""
        DELETE FROM user_fingerprints a
        USING user_fingerprints b
        WHERE a.user_id = b.user_id AND a.slot > b.slot
    """)

    op.drop_constraint('uq_user_fingerprints_user_slot', 'user_fingerprints', type_='unique')
    op.drop_index('ix_user_fingerprints_board', table_name='user_fingerprints')
    op.drop_index('ix_user_fingerprints_user_id', table_name='user_fingerprints')
    op.create_index('ix_user_fingerprints_user_id', 'user_fingerprints', ['user_id'], unique=True)

    for col in ('audio_seed', 'canvas_seed', 'device_memory', 'screen_height', 'screen_width',
                'chrome_major', 'retired_at', 'last_used_at', 'created_at',
                'session_count', 'board', 'slot'):
        op.drop_column('user_fingerprints', col)
