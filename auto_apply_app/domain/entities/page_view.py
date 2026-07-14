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
