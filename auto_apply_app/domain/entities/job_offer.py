from dataclasses import dataclass, field
from uuid import UUID
import hashlib
import re
from datetime import datetime
from typing import Optional

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.value_objects import ApplicationStatus, JobBoard
from auto_apply_app.domain.exceptions import JobPostingIdNotSetError

@dataclass
class JobOffer(Entity):
    url: str    
    form_url: str   
    company_name: str 
    job_title: str
    location: str
    job_board: JobBoard
    search_id: Optional[UUID] = field(default=None)
    user_id: Optional[UUID] = field(default=None)
    ranking: Optional[int] = field(default=None)    
    job_desc: Optional[str] = field(default=None)
    cover_letter: Optional[str] = field(default=None)  
    clean_title: Optional[str] = field(default=None) 
    _job_posting_id : Optional[str] = field(default=None, init=False) 
    application_date: Optional[datetime] = field(default=None)
    has_interview: bool = field(default=False)
    has_response: bool = field(default=False)
    status: ApplicationStatus = field(default=ApplicationStatus.FOUND)
    followup_date: Optional[datetime] = field(default=None) 

    def __post_init__(self):
        if self.search_id is None:
            raise ValueError("search_id must be provided for JobOffer")
        if self.user_id is None:
            raise ValueError("user_id must be provided for JobOffer")
        if self.company_name is None:
            raise ValueError("company_name must be provided for JobOffer")
        if self.job_title is None:
            raise ValueError("job_title must be provided for JobOffer")
        if self.job_board is None:
            raise ValueError("job_board must be provided for JobOffer")

        self._clean_location_string() # [NEW] Clean location upon creation
        self.set_job_posting_id()  # Generate job_posting_id on creation

    def _clean_location_string(self) -> None:
        """Removes trailing department/area codes (e.g., ' - 75', ' - 974') from the location."""
        if self.location:
            # Matches an optional space, a hyphen, an optional space, 
            # and 1 or more digits right at the end of the string ($)
            cleaned = re.sub(r'\s*(-\s*)?\d+\w*$', '', self.location)
            
            # Update the property and strip any accidental leftover whitespace
            self.location = cleaned.strip()

    def start_application(self) -> None:
        if self.status != ApplicationStatus.FOUND:
            raise ValueError("Only job offer with FOUND status you can apply for!")
        self.status = ApplicationStatus.IN_PROGRESS

    def update_response_status(self, has_response: bool) -> None:
        """Domain logic for tracking external responses"""
        self.has_response = has_response
        
    def update_interview_status(self, has_interview: bool) -> None:
        """Domain logic for tracking interviews"""
        self.has_interview = has_interview

    def mark_as_generated(self, cover_letter_text: str, clean_title: str = None) -> None:
        """Called by the Writer Node"""
        if self.status != ApplicationStatus.FOUND:
             raise ValueError("Can only generate content for FOUND jobs.")
        self.cover_letter = cover_letter_text
        if clean_title:
            self.clean_title = clean_title
        self.status = ApplicationStatus.GENERATED

    def approve_application(self) -> None:
        """Called by Premium User (Manual) or Router (Basic Auto)"""
        if self.status != ApplicationStatus.GENERATED:
            raise ValueError("Cannot approve a job that hasn't been generated yet.")
        self.status = ApplicationStatus.APPROVED

    def complete_application(self) -> None:
        """Called by Submitter Node"""
        if self.status != ApplicationStatus.APPROVED:
             raise ValueError("Job must be APPROVED before submission.")
        self.status = ApplicationStatus.SUBMITTED

    def _generate_fingerprint(self) -> str:
        company_name = self.company_name.replace(" ", "").lower().strip()
        job_title = self.job_title.replace(" ", "").lower().strip()
        user_id = str(self.user_id).strip()
        job_board = str(self.job_board.name).strip().lower()

        raw_string = f"{company_name}_{job_title}_{job_board}_{user_id}"
        return hashlib.md5(raw_string.encode()).hexdigest()

    def set_job_posting_id(self) -> None:
        """Uses the fingerprint as the ID."""
        self._job_posting_id = self._generate_fingerprint()

    def get_job_posting_id(self) -> str:
        """Getting job posting id"""
        if not self._job_posting_id:
            raise JobPostingIdNotSetError("Job posting id has not been set yet")
        return self._job_posting_id

    def is_submitted(self) -> bool:
        "Check whether application is submitted"
        return self.status == ApplicationStatus.SUBMITTED