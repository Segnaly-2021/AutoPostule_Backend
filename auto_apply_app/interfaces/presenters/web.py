"""
Web-specific presenters for formatting User and Auth data for the web UI.
"""

from typing import Optional, List, Dict 

from auto_apply_app.interfaces.presenters.base_presenter import AgentPresenter
from auto_apply_app.interfaces.viewmodels.agent_vm import AgentProgressViewModel, AgentViewModel
from auto_apply_app.application.dtos.user_dtos import UserResponse
from auto_apply_app.application.dtos.job_offer_dtos import JobOfferResponse
from auto_apply_app.application.dtos.subscription_dtos import UserSubscriptionResponse
from auto_apply_app.application.dtos.auth_user_dtos import LoginResponse
from auto_apply_app.interfaces.presenters.base_presenter import (
  UserPresenter, 
  JobPresenter, 
  JobSearchPresenter,
  SubPresenter,
  FreeSearchPresenter,
  AgentStatePresenter,
 
)

from auto_apply_app.domain.entities.agent_state import AgentState
from auto_apply_app.interfaces.viewmodels.agent_state_vm import (
    AgentStateViewModel,
    AgentStateMessageViewModel,
)

from auto_apply_app.interfaces.viewmodels.user_vm import (
  UserViewModel, 
  LoginViewModel,
  SubViewModel,
  UploadResumeViewModel
)
from auto_apply_app.interfaces.viewmodels.preferences_vm import (
    PreferencesViewModel, 
    CredentialViewModel
)
from auto_apply_app.interfaces.presenters.base_presenter import PreferencesPresenter
from auto_apply_app.application.dtos.preferences_dtos import UserPreferencesResponse
from auto_apply_app.interfaces.viewmodels.base import ErrorViewModel
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.application.dtos.agent_dtos import AgentResponse
from auto_apply_app.interfaces.viewmodels.job_search_vm import JobSearchViewModel
from auto_apply_app.domain.value_objects import ApplicationStatus
from auto_apply_app.interfaces.viewmodels.job_offer_vm import DailyStatsViewModel, JobOfferViewModel, DashboardViewModel, JobReviewViewModel
from auto_apply_app.interfaces.viewmodels.free_search_vm import (
    FreeSearchResultViewModel,
    JobSnippetViewModel
)




class WebUserPresenter(UserPresenter):
    """
    Concrete implementation of UserPresenter for Web/REST delivery.
    Converts Application DTOs into ViewModels.
    """

    def present_user(self, user: UserResponse) -> UserViewModel:
        return UserViewModel(
            id=user.res_id,
            full_name=f"{user.res_fname} {user.res_lname}".strip(),
            email=user.res_email,
            firstname=user.res_fname if user.res_fname else None,
            lastname=user.res_lname if user.res_lname else None,
            initials=f"{user.res_fname[0]}{user.res_lname[0]}".upper() if user.res_fname and user.res_lname else "",
            phone_number=user.res_phone_number if user.res_phone_number else None,
            resume_path=user.res_resume_path if user.res_resume_path else None,   
            # 🚨 NEW: Map the human-readable name from the DTO
            resume_file_name=user.res_resume_file_name, 
            address=user.res_address if user.res_address else None,
            current_position=user.res_current_position if user.res_current_position else None,
            current_company=user.res_current_company if user.res_current_company else None,
            school_type=user.res_school_type,
            graduation_year=user.res_graduation_year,
            major=user.res_major,
            study_level=user.res_study_level,
            linkedin_url=user.res_linkedin_url if user.res_linkedin_url else None,  # 🚨 NEW: Map LinkedIn URL
        )

    # ... (keep present_login and present_error as they are) ...

    # 🚨 NEW: Presenter method for the upload response
    def present_upload_resume_success(self, data: dict) -> UploadResumeViewModel:
        """
        Formats the dictionary returned by UploadUserResumeUseCase into a typed ViewModel.
        """
        return UploadResumeViewModel(
            message=data.get("message", "Success"),
            resume_path=data.get("resume_path", ""),
            resume_file_name=data.get("resume_file_name", "")
        )

    def present_login(self, login: LoginResponse) -> LoginViewModel:
        """
        Formats LoginResponse (token data) into a LoginViewModel.
        """
        return LoginViewModel(
            token=login.access_token,
            token_type=login.token_type,
            
        )

    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        """
        Standardizes error responses for the frontend.
        """
        return ErrorViewModel(
            message=message,
            code=error_code            
        )
    

class WebSubPresenter(SubPresenter):
    """
    Concrete implementation of SubPresenter for Web/REST delivery.
    Converts subscription DTOs into ViewModels.
    """

    def present_sub(self, subs: UserSubscriptionResponse | dict) -> SubViewModel:
        """
        Formats subscription data into a SubViewModel for the web.
        
        Handles two cases:
        1. UserSubscriptionResponse - full subscription entity
        2. dict - simple message response (e.g., webhook confirmations)
        """
        # Case 1: Full subscription entity
        if isinstance(subs, UserSubscriptionResponse):
            return SubViewModel(
                account=subs.account_type,
                is_active=subs.is_active,
                next_billing_date=subs.next_billing_date.isoformat() if subs.next_billing_date else None,
                start_date=subs.current_period_start.isoformat() if subs.current_period_start else None,
                exp_date=subs.current_period_end.isoformat() if subs.current_period_end else None,
                cancel_at=subs.cancel_at.isoformat() if subs.cancel_at else None,                
                message=None
            )
        
        # Case 2: Simple message response (dict)
        if isinstance(subs, dict):
            return SubViewModel(
                account=None,
                next_billing_date=None,
                start_date=None,
                exp_date=None,
                cancel_at=None,
                is_active=None,
                message=subs.get("message") or str(subs)
            )
        
        # Fallback (shouldn't happen, but be defensive)
        return SubViewModel(
            account=None,
            next_billing_date=None,
            start_date=None,
            exp_date=None,
            cancel_at=None,
            is_active=None,
            message="Subscription operation completed"
        )

    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        """
        Standardizes error responses for the frontend.
        """
        return ErrorViewModel(
            message=message,
            code=error_code
        )

class WebJobPresenter(JobPresenter):
    """Formats Job Offer data for the Web API."""

    def present_job(self, job: JobOfferResponse) -> JobOfferViewModel:
        """
        Converts a JobOffer domain entity into a ViewModel.
        """
        # Helper to format dates (handles both str and datetime)
        def format_date(date_value):
            if isinstance(date_value, str):
                # Already a string, extract just the date part
                return date_value.split('T')[0] if 'T' in date_value else date_value.split()[0]
            elif hasattr(date_value, 'date'):
                # It's a datetime object
                return date_value.date().isoformat()
            elif hasattr(date_value, 'isoformat'):
                # It's a date object
                return date_value.isoformat()
            return str(date_value)  # Fallback
        
        return JobOfferViewModel(
            id=job.id,
            company=job.company,
            title=job.clean_title if job.clean_title else job.title,  # Use clean_title if available for better FE charts
            cover_letter=job.coverLetter, 
            job_url=job.url,           
            location=job.location,
            interview=job.interview,
            response=job.response,     
            board=job.board, 
            status=job.status,  
            followUpDate=format_date(job.followUpDate),
            appliedDate=format_date(job.appliedDate)
        )
    
    def present_jobs(self, jobs: List[JobOfferResponse]) -> List[JobOfferViewModel]:
        """Batch conversion for multiple jobs."""
        return [self.present_job(job) for job in jobs]
    

    
    def present_job_for_review(self, job) -> JobReviewViewModel:
        """
        Special presenter method for the Review Page.
        Converts UUIDs and Enums to strings for FastAPI serialization.
        """
        return JobReviewViewModel(
            id=str(job.id),
            company_name=job.company_name,
            job_title=job.job_title,
            url=job.url,
            location=job.location,
            cover_letter=job.cover_letter or "",
            ranking=int(job.ranking)*10 or 50,
            board=str(job.job_board.value),
            status=str(job.status.value)
        )
    
    def present_daily_stats(self, data: dict) -> DailyStatsViewModel:
        """Formats the raw daily stats dictionary into a ViewModel."""
        return DailyStatsViewModel(
            count=data.get("count", 0)
        )
    
    def present_dashboard(self, data: Dict) -> DashboardViewModel:
        return DashboardViewModel(
            applications=self.present_jobs(data.get("applications", [])),
            total=data.get("total", 0),
            total_unfiltered=data.get("total_unfiltered", 0),  
            top_titles=data.get("top_titles", []),  
            page=data.get("page", 1),
            total_pages=data.get("total_pages", 0),
            limit=data.get("limit", 12)
        )

    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        return ErrorViewModel(message=message, code=error_code)
    



class WebPreferencesPresenter(PreferencesPresenter):
    """
    Concrete implementation of PreferencesPresenter for Web/REST delivery.
    """

    def present_preferences(self, response: UserPreferencesResponse) -> PreferencesViewModel:
        """
        Formats UserPreferencesResponse DTO into the specific JSON shape 
        expected by the React Settings component.
        """
        
        # ✅ Direct mapping from DTO to ViewModel
        credentials_vm = {}
        for board, cred_status_dto in response.credentials.items():
            credentials_vm[board] = CredentialViewModel(
                login=cred_status_dto.login,  # Always ""
                password=cred_status_dto.password,  # Always ""
                configured=cred_status_dto.configured
            )
        
        return PreferencesViewModel(
            isFullAutomation=response.is_full_automation,
            creativity=response.creativity_level,
            aiModel=response.ai_model, # ✅ NEW: Mapping from DTO
            boards=response.active_boards,
            credentials=credentials_vm
        )
        
    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        """
        Standardizes error responses for the frontend.
        """
        return ErrorViewModel(
            message=message,
            code=error_code
        )


class WebJobSearchPresenter(JobSearchPresenter):
    """Formats Job Search (Missions) data for the Web API."""

    def present_search(self, search: JobSearch, include_jobs: bool = False) -> JobSearchViewModel:
        """
        Converts a JobSearch domain entity into a ViewModel.
        
        Args:
            search: JobSearch entity
            include_jobs: If True, includes all job offers in the response
        """
        jobs_list = None
        if include_jobs and search.all_matched_jobs:
            # Use WebJobPresenter to format jobs
            job_presenter = WebJobPresenter()
            jobs_list = job_presenter.present_jobs(search.all_matched_jobs)
        
        # Calculate statistics
        total_jobs = len(search.all_matched_jobs) if search.all_matched_jobs else 0
        jobs_applied = sum(
            1 for job in (search.all_matched_jobs or []) 
            if job.status == ApplicationStatus.SUBMITTED
        )
        jobs_pending = sum(
            1 for job in (search.all_matched_jobs or []) 
            if job.status == ApplicationStatus.GENERATED
        )
        
        return JobSearchViewModel(
            id=str(search.id),
            user_id=str(search.user_id),
            job_title=search.job_title,
            job_board=search.job_board.value,
            status=search.status.value,
            started_at=search.created_at.isoformat() if search.created_at else None,
            completed_at=search.updated_at.isoformat() if search.updated_at else None,
            total_jobs_found=total_jobs,
            jobs_applied=jobs_applied,
            jobs_pending_review=jobs_pending,
            jobs=jobs_list
        )

    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        return ErrorViewModel(message=message, code=error_code)
    
class WebAgentPresenter(AgentPresenter):
    """
    Concrete implementation of AgentPresenter for Web/REST delivery.
    """

    def present_agent_result(self, result: AgentResponse) -> AgentViewModel:
        return AgentViewModel(
            search_id=result.search_id,
            status=result.status,
            message=result.message
        )
    
    def present_progress(self, progress_data: dict) -> AgentProgressViewModel:
        """
        Formats real-time progress data for SSE streaming.
        Now supports parallel worker sources and fatal errors.
        """
        return AgentProgressViewModel(
            source=progress_data.get("source", "MASTER"), # Default to Master if missing
            stage=progress_data.get("stage"),
            node=progress_data.get("node"),
            status=progress_data.get("status", "in_progress"),
            search_id=progress_data.get("search_id"),
            progress_percent=progress_data.get("progress_percent"),
            error=progress_data.get("error") # Passes the Circuit Breaker error to UI
        )

    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        return ErrorViewModel(message=message, code=error_code)
    




class WebFreeSearchPresenter(FreeSearchPresenter):
    """
    Concrete implementation for presenting free agent search results.
    Formats the raw search output into the JSON structure expected by React.
    """
    
    def present_search_results(self, search_output: Dict) -> FreeSearchResultViewModel:
        """
        Transform the fake agent's output into a ViewModel.
        
        Args:
            search_output: Dict from FakeMasterAgent.search_all_boards()
                {
                    "jobs": [job.to_dict(), ...],
                    "total_found": int,
                    "boards_searched": [str, ...],
                    "status": str
                }
        
        Returns:
            FreeSearchResultViewModel ready for JSON serialization
        """
        
        # Map each job dict to a ViewModel
        jobs_vm = []
        for job_dict in search_output.get("jobs", []):
            job_vm = JobSnippetViewModel(
                jobTitle=job_dict.get("job_title", ""),
                companyName=job_dict.get("company_name", ""),
                location=job_dict.get("location", ""),
                descriptionSnippet=job_dict.get("description_snippet", ""),
                jobBoard=job_dict.get("job_board", ""),
                url=job_dict.get("url", "")
            )
            jobs_vm.append(job_vm)
        
        return FreeSearchResultViewModel(
            jobs=jobs_vm,
            totalFound=search_output.get("total_found", 0),
            boardsSearched=search_output.get("boards_searched", []),
            status=search_output.get("status", "error"),
            errorMessage=search_output.get("error_message", "")
        )
    


class WebAgentStatePresenter(AgentStatePresenter):

    def present_state(self, agent_state: AgentState) -> AgentStateViewModel:
        return AgentStateViewModel(
            isShutdown=agent_state.is_shutdown,
            searchId=str(agent_state.search_id) if agent_state.search_id else None,
        )

    def present_message(self, message: str, agent_state: AgentState) -> AgentStateMessageViewModel:
        return AgentStateMessageViewModel(
            message=message,
            isShutdown=agent_state.is_shutdown,
            searchId=str(agent_state.search_id) if agent_state.search_id else None,
        )

    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        return ErrorViewModel(
            message=message,
            code=error_code,
        )