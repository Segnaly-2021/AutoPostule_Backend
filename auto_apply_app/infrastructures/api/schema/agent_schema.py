# auto_apply_app/infrastructures/api/schema/agent_schema.py
from pydantic import BaseModel, Field
from typing import List, Optional

from auto_apply_app.domain.value_objects import ContractType

class StartAgentRequest(BaseModel):
    """
    Request to start the parallel job search agent.
    
    Note: user_id is NOT included - it's extracted from the JWT token.
    """
    job_title: str = Field(..., min_length=1, max_length=200, description="Desired job title")
    
    # 🚨 V2 UPDATE: Renamed to plural `job_boards` to match the Controller/DTO
    job_boards: List[str] = Field(..., description="List of job boards to search on (e.g., ['WTTJ', 'APEC', 'HELLOWORK'])")   
    
    contract_types: List[ContractType] = Field(default=None, description="Acceptable contract types")
    resume_path: Optional[str] = Field(default=None, description="Path to the user's resume (if not using default)")
    min_salary: Optional[int] = Field(default=0, description="Minimum salary expectation")
    location: Optional[str] = Field(default=None, description="Preferred job locations")
    
    class Config:
        json_schema_extra = {
            "example": {
                "job_title": "Senior Python Developer",
                "job_boards": ["WTTJ", "APEC"], # ✅ Updated example
                "contract_types": ["CDI", "Alternance"],
                "location": "Paris",
                "min_salary": 50000,    
                "resume_path": "/path/to/resume.pdf"
            }
        }


class ResumeAgentRequest(BaseModel):
    """
    Request to resume a paused job search workflow.
    Used when Premium users want to apply to reviewed drafts.
    """
    apply_all: bool = Field(
        default=True, 
        description="If True, apply to all GENERATED drafts. If False, only apply to APPROVED jobs."
    )

    class Config:
        json_schema_extra = {
            "example": {
                "apply_all": True
            }
        }


class AgentViewModel(BaseModel):
    """Response model for basic agent operations."""
    message: str
    search_id: Optional[str] = None
    status: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Parallel Agent successfully started",
                "search_id": "123e4567-e89b-12d3-a456-426614174000",
                "status": "started"
            }
        }


class ProgressEventViewModel(BaseModel):
    """
    SSE progress event model.
    Sent during real-time streaming to drive the frontend UI.
    """
    # 🚨 V2 UPDATE: Added the parallel architecture UI fields!
    source: str = Field(default="MASTER", description="The worker node emitting the event (e.g., 'MASTER', 'APEC', 'WTTJ')")
    stage: str = Field(..., description="Current workflow stage")
    node: Optional[str] = Field(None, description="Internal LangGraph node name (for debugging)")
    status: str = Field(..., description="Status of the current stage (in_progress, success, error)")
    search_id: str = Field(..., description="UUID of the job search")
    progress_percent: Optional[int] = Field(None, description="Completion percentage (0-100)")
    error: Optional[str] = Field(None, description="Circuit breaker error message if the worker crashed")

    class Config:
        json_schema_extra = {
            "example": {
                "source": "APEC",
                "stage": "Scraping Jobs",
                "node": "scrape",
                "status": "in_progress",
                "search_id": "123e4567-e89b-12d3-a456-426614174000",
                "progress_percent": 45,
                "error": None
            }
        }