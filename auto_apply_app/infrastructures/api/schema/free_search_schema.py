# auto_apply_app/infrastructures/api/schema/free_search_schema.py

from pydantic import BaseModel, Field, field_validator
from typing import List


class FreeSearchRequestSchema(BaseModel):
    """
    Request schema for free tier job search.
    """
    query: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Job search term (e.g., 'Product Manager')",
        examples=["Software Engineer", "Marketing Manager"]
    )
    targetCount: int = Field(
        ...,
        description="Number of jobs to find (10, 20, or 50)",
        examples=[10, 20, 50]
    )
    
    @field_validator('targetCount')
    @classmethod
    def validate_target_count(cls, v):
        if v not in [10, 20, 50]:
            raise ValueError('targetCount must be 10, 20, or 50')
        return v
    
    @field_validator('query')
    @classmethod
    def validate_query(cls, v):
        if not v.strip():
            raise ValueError('query cannot be empty or whitespace')
        return v.strip()


class JobSnippetSchema(BaseModel):
    """
    Schema for a single job snippet in search results.
    """
    jobTitle: str
    companyName: str
    location: str
    descriptionSnippet: str
    jobBoard: str
    url: str


class FreeSearchResponseSchema(BaseModel):
    """
    Response schema for free search results.
    """
    jobs: List[JobSnippetSchema]
    totalFound: int
    boardsSearched: List[str]
    status: str
    errorMessage: str = ""