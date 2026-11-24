# ==============================================================================
# FILE: auto_apply_app/domain/entities/job_snippet.py
#
# WHAT IT DOES:
#   Lightweight job preview returned by the free search. It is never stored and
#   does not extend Entity, so it has no id or application tracking.
#
# USED BY:
#   - auto_apply_app/infrastructures/agent/fake_agent/fake_workers/fake_apec_worker.py: search results
#   - auto_apply_app/infrastructures/agent/fake_agent/fake_workers/fake_hw_worker.py: search results
#   - auto_apply_app/infrastructures/agent/fake_agent/fake_workers/fake_indeed_worker.py: search results
#   - auto_apply_app/infrastructures/agent/fake_agent/fake_workers/fake_wttj_worker.py: search results
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
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