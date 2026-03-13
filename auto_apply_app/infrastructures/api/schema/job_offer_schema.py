# In auto_apply_app/infrastructures/api/schema/job_offer_schema.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date

class ApplicationFilters(BaseModel):
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    board: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    has_response: Optional[bool] = None
    has_interview: Optional[bool] = None

class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=12, ge=1, le=100)

class StatusUpdateSchema(BaseModel):
    # Reusable for both response and interview
    status: bool

class AnalyticsViewModel(BaseModel):
    total_applications: int
    period_applications: int
    responses: int
    interviews: int
    by_location: list[dict[str, int | str]]  # [{"name": "Python", "count": 5}]