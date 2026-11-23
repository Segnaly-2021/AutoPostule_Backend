# ==============================================================================
# FILE: auto_apply_app/domain/entities/page_view.py
#
# WHAT IT DOES:
#   One anonymous page view, the raw data behind the admin visitor metrics.
#
# USED BY:
#   - auto_apply_app/application/repositories/page_view_repo.py: repository interface
#   - auto_apply_app/application/use_cases/analytics_use_cases.py: records views
#   - auto_apply_app/infrastructures/persistence/database/repositories/page_view_repo_db.py: persistence
#   - auto_apply_app/infrastructures/persistence/in_memory/memory.py: in-memory store
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity


@dataclass
class PageView(Entity):
    """
    A single anonymous page view, used to answer "how many visitors today".

    Deliberately holds no PII: no IP, no user agent, no cookies. `visitor_id` is an
    opaque client-generated value, and `referrer` is reduced to a bare host before it
    ever reaches this entity — a full referrer URL can carry tokens in its query string.
    """

    visitor_id: str
    path: str
    user_id: Optional[UUID] = None
    referrer: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
