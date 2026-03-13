# auto_apply_app/application/dtos/agent_dtos.py
from dataclasses import dataclass
from uuid import UUID
from typing import List
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.value_objects import JobBoard, ContractType

@dataclass(frozen=True)
class StartAgentRequest:
    user_id: str
    job_title: str
    job_boards: List[str]
    location: str = None
    contract_types: list[ContractType] = None
    min_salary: int = None
    resume_path: str = None

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("user_id is required")
        
        if not self.job_title:
            raise ValueError("job_title is required")
          
        if not self.job_boards or len(self.job_boards) == 0:
            raise ValueError("job_board is required")
        
    
    def to_execution_params(self):
        boards = {
            "apec": JobBoard.APEC, 
            "hellowork": JobBoard.HELLOWORK, 
            "wttj": JobBoard.WTTJ
        }
        return {
            "user_id": self.user_id,
            "job_title": self.job_title,
            "job_boards": [boards.get(board.lower(), None) for board in self.job_boards],
            "resume_path": self.resume_path,
            "location": self.location.strip() if self.location else "",
            "min_salary": int(self.min_salary),
            "contract_types": self.contract_types or []
        }


@dataclass(frozen=True)
class ResumeAgentRequest:
    """
    DTO for resuming a paused job search workflow.
    Used when Premium users approve draft applications.
    """
    user_id: str
    search_id: str
    apply_all: bool = True

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("user_id is required")
        
        if not self.search_id:
            raise ValueError("search_id is required")
    
    def to_execution_params(self):
        return {
            "user_id": UUID(self.user_id),
            "search_id": UUID(self.search_id),
            "apply_all": self.apply_all
        }


@dataclass(frozen=True)
class KillAgentRequest:
    """
    DTO for killing a running job search.
    Emergency stop operation.
    """
    user_id: str
    search_id: str

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("user_id is required")
        
        if not self.search_id:
            raise ValueError("search_id is required")
    
    def to_execution_params(self):
        return {
            "user_id": UUID(self.user_id),
            "search_id": UUID(self.search_id)
        }
    



@dataclass(frozen=True)
class AgentResponse:
    """
    This class is a data carrier from our Domain/Service layer -> to the Application layer
    It standardizes the output of Agent operations (Start, Resume, Kill).
    """
    search_id: str
    status: str
    message: str

    @classmethod
    def from_job_search(cls, search: JobSearch, status: str, message: str) -> "AgentResponse":
        """
        Factory method to build the response directly from the Entity 
        and the execution context (status/message).
        """
        return cls(
            search_id=str(search.id),
            status=status,
            message=message
        )