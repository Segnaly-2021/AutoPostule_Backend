from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from auto_apply_app.application.dtos.announcement_dtos import (
    MAX_BODY_LENGTH,
    MAX_ICON_LENGTH,
    MAX_LABEL_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
)


class AnnouncementSchema(BaseModel):
    """What a normal user's modal receives.

    Both languages travel together rather than being resolved server-side: the
    client owns the language, i18next can switch it without a refetch, and this
    is two short strings.
    """

    id: str
    title_fr: str
    title_en: str
    body_fr: str
    body_en: str
    cta_label_fr: Optional[str] = None
    cta_label_en: Optional[str] = None
    cta_url: Optional[str] = None
    icon: Optional[str] = None


class AdminAnnouncementSchema(AnnouncementSchema):
    """Adds what the composer needs and users never see."""

    is_published: bool
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AnnouncementWriteSchema(BaseModel):
    """Body of the admin create/update call.

    The bounds here mirror the DTO's caps and the column widths. They stop an
    oversized post at the edge; the entity still enforces the rules that are about
    meaning rather than size (a CTA is a url plus both labels, or nothing).
    """

    title_fr: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH)
    title_en: str = Field(..., min_length=1, max_length=MAX_TITLE_LENGTH)
    body_fr: str = Field(..., min_length=1, max_length=MAX_BODY_LENGTH)
    body_en: str = Field(..., min_length=1, max_length=MAX_BODY_LENGTH)
    cta_label_fr: Optional[str] = Field(None, max_length=MAX_LABEL_LENGTH)
    cta_label_en: Optional[str] = Field(None, max_length=MAX_LABEL_LENGTH)
    cta_url: Optional[str] = Field(None, max_length=MAX_URL_LENGTH)
    icon: Optional[str] = Field(None, max_length=MAX_ICON_LENGTH)
    is_published: bool = False
    starts_at: Optional[datetime] = Field(
        None, description="Defaults to now. Set it ahead to schedule the notice."
    )
    ends_at: Optional[datetime] = Field(
        None, description="Null means open-ended."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "title_fr": "Barre de progression détaillée",
                "title_en": "Granular progress bar",
                "body_fr": "Vous suivez maintenant chaque étape de l'agent en temps réel.",
                "body_en": "You can now follow every step the agent takes, live.",
                "cta_label_fr": "Voir mes candidatures",
                "cta_label_en": "See my applications",
                "cta_url": "/job-search/dashboard",
                "icon": "🚀",
                "is_published": True,
            }
        }
