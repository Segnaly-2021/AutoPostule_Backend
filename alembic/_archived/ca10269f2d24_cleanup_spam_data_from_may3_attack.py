# ==============================================================================
# FILE: alembic/_archived/ca10269f2d24_cleanup_spam_data_from_may3_attack.py
#
# WHAT IT DOES:
#   ARCHIVED data migration, with no schema change. Permanently deletes every user
#   whose auth row was created between 16:00 and 19:00 UTC on 2026-05-03; foreign
#   key cascades remove their data. The downgrade cannot restore them.
#
# USED BY:
#   - None: archived. Alembic only loads alembic/versions, so this no longer runs.
#
# EXTERNAL DEPENDENCIES:
#   - Alembic: migration framework
# ==============================================================================

"""cleanup_spam_data_from_may3_attack

Revision ID: ca10269f2d24
Revises: 683ff825b625
Create Date: 2026-05-07 14:16:56.838056

"""
from typing import Sequence, Union

from alembic import op



# revision identifiers, used by Alembic.
revision: str = 'ca10269f2d24'
down_revision: Union[str, Sequence[str], None] = '683ff825b625'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """
    Delete spam users and their data from the May 3rd 2026 attack.
    
    Attack window: 2026-05-03 16:21 UTC to 2026-05-03 18:33 UTC.
    
    CASCADE on users.id deletes auth_users, subscriptions, preferences,
    job_searches, job_offers, board_credentials, fingerprint, agent_state.
    """
    op.execute("""
        DELETE FROM users
        WHERE id IN (
            SELECT u.id 
            FROM users u
            JOIN auth_users a ON a.user_id = u.id
            WHERE a.created_at >= '2026-05-03 16:00:00+00'
              AND a.created_at <= '2026-05-03 19:00:00+00'
        );
    """)


def downgrade() -> None:
    pass