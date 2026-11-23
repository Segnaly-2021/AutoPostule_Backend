# ==============================================================================
# FILE: alembic/versions/cc39489ef754_add_pending_email_to_auth_users.py
#
# WHAT IT DOES:
#   Active migration. Adds the nullable auth_users.pending_email column, which
#   holds a new address until the email-change flow verifies it.
#
# USED BY:
#   - Alembic (alembic/env.py, scripts/migrate.sh): applied by `alembic upgrade`
#
# EXTERNAL DEPENDENCIES:
#   - Alembic: migration framework
#   - SQLAlchemy: column and type definitions
# ==============================================================================

"""add pending_email to auth_users

Revision ID: cc39489ef754
Revises: 6dc1a09af310
Create Date: 2026-06-20 18:23:22.417705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc39489ef754'
down_revision: Union[str, Sequence[str], None] = '6dc1a09af310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('auth_users', sa.Column('pending_email', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('auth_users', 'pending_email')