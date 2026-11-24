# ==============================================================================
# FILE: alembic/_archived/c4e1a9b7d2f8_add_pending_email_to_auth_users.py
#
# WHAT IT DOES:
#   ARCHIVED migration. Adds the nullable auth_users.pending_email column. The
#   live chain makes the same change in alembic/versions/cc39489ef754.
#
# USED BY:
#   - None: archived. Alembic only loads alembic/versions, so this no longer runs.
#
# EXTERNAL DEPENDENCIES:
#   - Alembic: migration framework
#   - SQLAlchemy: column and type definitions
# ==============================================================================

"""add pending_email to auth_users

Revision ID: c4e1a9b7d2f8
Revises: efd03edb878e
Create Date: 2026-06-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e1a9b7d2f8'
down_revision: Union[str, Sequence[str], None] = 'efd03edb878e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('auth_users', sa.Column('pending_email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('auth_users', 'pending_email')
