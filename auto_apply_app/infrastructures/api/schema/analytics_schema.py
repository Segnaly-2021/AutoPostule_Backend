from typing import Optional

from pydantic import BaseModel, Field


class PageViewCreateSchema(BaseModel):
    """
    Body of the public page-view tracking call.

    This is untrusted input from an unauthenticated endpoint, so the fields are tightly
    bounded here and scrubbed again in RecordPageViewRequest (query strings stripped,
    referrer reduced to a host, id-like path segments collapsed).
    """

    visitor_id: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]{8,64}$",
        description="Opaque, client-generated, first-party identifier. Not derived from "
                    "any personal data.",
    )
    path: str = Field(..., min_length=1, max_length=200)
    referrer: Optional[str] = Field(None, max_length=200)

    class Config:
        json_schema_extra = {
            "example": {
                "visitor_id": "a7Fq2Kd9xLm3",
                "path": "/pricing",
                "referrer": "https://www.google.com",
            }
        }
