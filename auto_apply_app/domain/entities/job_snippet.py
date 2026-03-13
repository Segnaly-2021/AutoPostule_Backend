# auto_apply_app/domain/entities/job_snippet.py

from dataclasses import dataclass
from auto_apply_app.domain.value_objects import JobBoard


@dataclass
class JobSnippet:
    """
    Lightweight job data for preview/free tier.
    No persistence, no application tracking - just display info.
    """
    job_title: str
    company_name: str
    location: str
    description_snippet: str  # 2-3 sentences max
    job_board: JobBoard
    url: str  # Link to the actual job posting
    
    def to_dict(self):
        """Convert to JSON-serializable dict for API response."""
        return {
            "job_title": self.job_title,
            "company_name": self.company_name,
            "location": self.location,
            "description_snippet": self.description_snippet,
            "job_board": self.job_board.name,  # Enum to string
            "url": self.url
        }