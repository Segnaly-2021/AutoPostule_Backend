"""
DTOs for anonymous visitor tracking.

The page-view endpoint is unauthenticated by necessity — anonymous visitors are the
whole point — so this DTO is the boundary where untrusted client input is scrubbed
before it can reach the database.
"""

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

MAX_PATH_LENGTH = 200
MAX_REFERRER_LENGTH = 200

_VISITOR_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Path segments that are identifiers rather than routes. Collapsing them keeps the
# cardinality of `path` bounded (/applications/:id, not one row per application) and
# stops a caller from smuggling arbitrary payloads into the column.
_UUID_SEGMENT = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_NUMERIC_SEGMENT = re.compile(r"^\d+$")


def _normalize_path(raw: str) -> str:
    """Strip the query string and collapse id-like segments to ':id'."""
    path = urlparse(raw).path or "/"
    segments = [
        ":id" if (_UUID_SEGMENT.match(seg) or _NUMERIC_SEGMENT.match(seg)) else seg
        for seg in path.split("/")
    ]
    return "/".join(segments)[:MAX_PATH_LENGTH]


def _normalize_referrer(raw: Optional[str]) -> Optional[str]:
    """
    Reduce a referrer to its bare host.

    A full referrer URL routinely carries tokens and PII in its query string; we only
    ever want to know which site sent the visitor, so we keep the host and drop the rest.
    """
    if not raw:
        return None
    host = urlparse(raw).netloc or raw
    return host[:MAX_REFERRER_LENGTH] or None


@dataclass(frozen=True)
class RecordPageViewRequest:
    visitor_id: str
    path: str
    referrer: Optional[str] = None
    user_id: Optional[str] = None

    def __post_init__(self):
        if not _VISITOR_ID_PATTERN.match(self.visitor_id or ""):
            raise ValueError("visitor_id must be 8-64 characters of [A-Za-z0-9_-]")

        if not self.path:
            raise ValueError("path is required")

        # frozen dataclass: normalized values have to go in via object.__setattr__
        object.__setattr__(self, "path", _normalize_path(self.path))
        object.__setattr__(self, "referrer", _normalize_referrer(self.referrer))

        if not self.path.startswith("/"):
            raise ValueError("path must start with '/'")
