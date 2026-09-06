"""
DTOs for in-app announcements.

The write side is the first admin *write* surface in the app, so this is where the
input conventions for it get set: the DTO carries raw strings from the request and
the entity validates them. Two layers rather than one because the entity's rules
(a CTA is url + both labels, or nothing) apply to an edit made anywhere, while the
length caps below exist only to bound what an HTTP client can post.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from auto_apply_app.domain.entities.announcement import Announcement

MAX_TITLE_LENGTH = 160
MAX_BODY_LENGTH = 4000
MAX_LABEL_LENGTH = 80
MAX_URL_LENGTH = 500
MAX_ICON_LENGTH = 16


@dataclass(frozen=True)
class AnnouncementResponse:
    """What a normal user's modal renders.

    Both languages are sent rather than one resolved server-side: the client owns
    the language, i18next can switch it without a refetch, and the payload is two
    short strings.
    """

    id: str
    title_fr: str
    title_en: str
    body_fr: str
    body_en: str
    cta_label_fr: Optional[str]
    cta_label_en: Optional[str]
    cta_url: Optional[str]
    icon: Optional[str]

    @classmethod
    def from_entity(cls, a: Announcement) -> "AnnouncementResponse":
        return cls(
            id=str(a.id),
            title_fr=a.title_fr,
            title_en=a.title_en,
            body_fr=a.body_fr,
            body_en=a.body_en,
            cta_label_fr=a.cta_label_fr,
            cta_label_en=a.cta_label_en,
            cta_url=a.cta_url,
            icon=a.icon,
        )


@dataclass(frozen=True)
class AdminAnnouncementResponse(AnnouncementResponse):
    """Everything the composer needs, including what users never see.

    Separate from AnnouncementResponse so publication state and the schedule
    cannot leak onto the public endpoint by someone adding a field in one place.
    """

    is_published: bool = False
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_entity(cls, a: Announcement) -> "AdminAnnouncementResponse":
        return cls(
            id=str(a.id),
            title_fr=a.title_fr,
            title_en=a.title_en,
            body_fr=a.body_fr,
            body_en=a.body_en,
            cta_label_fr=a.cta_label_fr,
            cta_label_en=a.cta_label_en,
            cta_url=a.cta_url,
            icon=a.icon,
            is_published=a.is_published,
            starts_at=a.starts_at.isoformat(),
            ends_at=a.ends_at.isoformat() if a.ends_at else None,
            created_at=a.created_at.isoformat(),
            updated_at=a.updated_at.isoformat(),
        )


def _clean(value: Optional[str], cap: int) -> Optional[str]:
    """Trim, cap, and turn an empty string into None.

    The empty-to-None step matters: an HTML form posts "" for a cleared optional
    field, and "" would satisfy the entity's "is a CTA present?" check while
    rendering a button with no label.
    """
    if value is None:
        return None
    trimmed = value.strip()[:cap]
    return trimmed or None


@dataclass(frozen=True)
class SaveAnnouncementRequest:
    """Create when announcement_id is None, update otherwise."""

    title_fr: str
    title_en: str
    body_fr: str
    body_en: str
    cta_label_fr: Optional[str] = None
    cta_label_en: Optional[str] = None
    cta_url: Optional[str] = None
    icon: Optional[str] = None
    is_published: bool = False
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    announcement_id: Optional[UUID] = None

    def cleaned(self) -> "SaveAnnouncementRequest":
        return SaveAnnouncementRequest(
            title_fr=(self.title_fr or "").strip()[:MAX_TITLE_LENGTH],
            title_en=(self.title_en or "").strip()[:MAX_TITLE_LENGTH],
            body_fr=(self.body_fr or "").strip()[:MAX_BODY_LENGTH],
            body_en=(self.body_en or "").strip()[:MAX_BODY_LENGTH],
            cta_label_fr=_clean(self.cta_label_fr, MAX_LABEL_LENGTH),
            cta_label_en=_clean(self.cta_label_en, MAX_LABEL_LENGTH),
            cta_url=_clean(self.cta_url, MAX_URL_LENGTH),
            icon=_clean(self.icon, MAX_ICON_LENGTH),
            is_published=self.is_published,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            announcement_id=self.announcement_id,
        )
