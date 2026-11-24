# ==============================================================================
# FILE: alembic/_archived/f74ac0ee9955_add_jobteaser_to_active_boards_json.py
#
# WHAT IT DOES:
#   ARCHIVED data migration, with no schema change. Adds "jobteaser": false to the
#   user_preferences.active_boards JSONB wherever the key is missing.
#
# USED BY:
#   - None: archived. Alembic only loads alembic/versions, so this no longer runs.
#
# EXTERNAL DEPENDENCIES:
#   - Alembic: migration framework
#   - SQLAlchemy: imported by the template; this migration uses raw SQL
# ==============================================================================

"""add jobteaser to active_boards json

Revision ID: f74ac0ee9955
Revises: 7ca91500db7e
Create Date: 2026-04-28 22:36:57.326748

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f74ac0ee9955'
down_revision: Union[str, Sequence[str], None] = '7ca91500db7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # We delete all the auto-generated drops so LangGraph survives!
    # And we add our custom JSONB update.
    op.execute(
        """
        UPDATE user_preferences 
        SET active_boards = active_boards || '{"jobteaser": false}'::jsonb 
        WHERE active_boards IS NOT NULL 
        AND NOT (active_boards ? 'jobteaser');
        """
    )

def downgrade() -> None:
    op.execute(
        """
        UPDATE user_preferences 
        SET active_boards = active_boards - 'jobteaser' 
        WHERE active_boards IS NOT NULL 
        AND active_boards ? 'jobteaser';
        """
    )