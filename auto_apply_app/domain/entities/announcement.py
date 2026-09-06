from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.exceptions import ValidationError


@dataclass
class Announcement(Entity):
    """
    An in-app notice -- a release, a new feature -- shown once per user in a modal.

    Content lives in the database rather than in the code because the point is
    publishing one without a deploy. It is bilingual by field, not by blob: the
    app is FR/EN everywhere, and a missing translation should fail here rather
    than render an empty modal to half the users.

    `is_published` and the `starts_at`/`ends_at` window are separate controls.
    is_published is the author's switch -- it is how a half-written draft stays
    invisible even though its window is open. The window is the schedule. Both
    have to hold, which is what `is_live_at` encodes.
    """

    title_fr: str
    title_en: str
    body_fr: str
    body_en: str

    # A notice does not have to lead anywhere. When cta_url is absent the modal
    # renders a dismiss button alone, so the labels travel with it.
    cta_label_fr: Optional[str] = None
    cta_label_en: Optional[str] = None
    cta_url: Optional[str] = None

    icon: Optional[str] = None

    is_published: bool = False
    starts_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # None means open-ended. A far-future sentinel would read as a real date in
    # the admin list and would eventually arrive.
    ends_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Reject what the modal cannot render.

        Called on construction and after every edit, because this is the first
        admin *write* surface in the app -- there is no existing validation layer
        to lean on, and a bad row here is visible to every logged-in user at once.
        """
        for name in ("title_fr", "title_en", "body_fr", "body_en"):
            if not (getattr(self, name) or "").strip():
                raise ValidationError(f"Announcement.{name} is required.")

        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValidationError("Announcement ends_at must be after starts_at.")

        # A CTA is a link plus a label in both languages, or nothing at all. A URL
        # with no label renders an unlabelled button; a label with no URL renders a
        # button that does nothing.
        cta_parts = (self.cta_url, self.cta_label_fr, self.cta_label_en)
        if any(p and p.strip() for p in cta_parts) and not all(
            p and p.strip() for p in cta_parts
        ):
            raise ValidationError(
                "Announcement CTA needs url, cta_label_fr and cta_label_en together."
            )

        if self.cta_url and not self.cta_url.startswith(("/", "http://", "https://")):
            # Blocks javascript: and data:, which would otherwise be injected into
            # an href rendered for every logged-in user.
            raise ValidationError(
                "Announcement cta_url must be a relative path or an http(s) URL."
            )

    def is_live_at(self, moment: datetime) -> bool:
        """Both controls must hold -- see the class docstring."""
        if not self.is_published:
            return False
        if moment < self.starts_at:
            return False
        return self.ends_at is None or moment < self.ends_at

    def publish(self) -> None:
        self.validate()
        self.is_published = True
        self.updated_at = datetime.now(timezone.utc)

    def unpublish(self) -> None:
        """Pulls it from every user's screen on their next page load.

        Deliberately not a delete: the dismissals already recorded against it stay
        valid, so re-publishing does not re-show it to people who dismissed it.
        """
        self.is_published = False
        self.updated_at = datetime.now(timezone.utc)

    def touch(self) -> None:
        self.validate()
        self.updated_at = datetime.now(timezone.utc)
