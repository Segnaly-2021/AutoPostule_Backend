# ==============================================================================
# FILE: alembic/versions/6dc1a09af310_baseline_current_schema.py
#
# WHAT IT DOES:
#   Active migration and root of the live chain. Makes no schema change: it marks
#   the schema that already existed as the starting point. A brand-new empty
#   database would therefore NOT get the base tables from this chain.
#
# USED BY:
#   - Alembic (alembic/env.py, scripts/migrate.sh): applied by `alembic upgrade`
#
# EXTERNAL DEPENDENCIES:
#   - Alembic: reads the revision identifiers below
# ==============================================================================

"""baseline current schema

Revision ID: 6dc1a09af310
Revises: 
Create Date: 2026-06-20 18:10:23.108961

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '6dc1a09af310'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Baseline — schema already exists in DB. No-op."""
    pass


def downgrade() -> None:
    """Baseline — no-op."""
    pass