from abc import ABC, abstractmethod
from typing import Optional, Dict

from auto_apply_app.application.dtos.user_dtos import UserResponse
from auto_apply_app.application.dtos.subscription_dtos import UserSubscriptionResponse
from auto_apply_app.application.dtos.preferences_dtos import UserPreferencesResponse
from auto_apply_app.interfaces.viewmodels.user_vm import (
  UserViewModel, 
  LoginViewModel,
  SubViewModel,
  UploadResumeViewModel
)


from auto_apply_app.interfaces.viewmodels.base import ErrorViewModel
from auto_apply_app.application.dtos.auth_user_dtos import LoginResponse
from auto_apply_app.application.dtos.job_offer_dtos import JobOfferResponse
from auto_apply_app.application.dtos.job_search_dtos import JobSearchResponse
from auto_apply_app.interfaces.viewmodels.job_offer_vm import JobOfferViewModel, DashboardViewModel
from auto_apply_app.interfaces.viewmodels.job_search_vm import JobSearchViewModel
from auto_apply_app.interfaces.viewmodels.agent_vm import AgentViewModel
from auto_apply_app.domain.entities.agent_state import AgentState
from auto_apply_app.interfaces.viewmodels.agent_state_vm import (
    AgentStateViewModel,
    AgentStateMessageViewModel,
)
from auto_apply_app.interfaces.viewmodels.preferences_vm import PreferencesViewModel
from auto_apply_app.interfaces.viewmodels.free_search_vm import FreeSearchResultViewModel

class UserPresenter(ABC):

  @abstractmethod
  def present_user(self, user: UserResponse) -> UserViewModel:
    pass


  @abstractmethod
  def present_message(self, value) -> MessageViewModel:
    pass

  # Add this to your abstract UserPresenter class
  def present_upload_resume_success(self, data: dict) -> UploadResumeViewModel:
      pass

  @abstractmethod
  def present_login(self, user: LoginResponse) -> LoginViewModel:
    pass


  @abstractmethod
  def present_error(self, message: str, error_code: Optional[str]=None) -> ErrorViewModel:
    pass

class JobPresenter(ABC):
  @abstractmethod
  def present_job(self, job: JobOfferResponse) -> JobOfferViewModel:
    pass

  @abstractmethod
  def present_jobs(self, job: JobOfferResponse):
    pass

  @abstractmethod
  def present_dashboard(self, data: Dict) -> DashboardViewModel:
    pass


  @abstractmethod
  def present_error(self, message: str, error_code: Optional[str]=None) -> ErrorViewModel:
    pass


class JobSearchPresenter(ABC):
  @abstractmethod
  def present_search(self, search: JobSearchResponse) -> JobSearchViewModel:
    pass

  @abstractmethod
  def present_error(self, message: str, error_code: Optional[str]=None) -> ErrorViewModel:
    pass



class SubPresenter(ABC):
  @abstractmethod
  def present_sub(self, subs: UserSubscriptionResponse | dict) -> SubViewModel:
    pass

  @abstractmethod
  def present_error(self, message: str, error_code: Optional[str]=None) -> ErrorViewModel:
    pass





class AgentPresenter(ABC):
    """Abstract base presenter for agent operations."""
    
    @abstractmethod
    def present_agent_result(self, result: dict) -> AgentViewModel:
        """Format agent operation result into ViewModel."""
        pass
    
    def present_progress(self, progress_data: dict) -> dict:
        """
        Formats real-time progress data for SSE streaming.
        
        This is a simple pass-through since SSE sends raw dicts.
        You could wrap it in AgentProgressViewModel if you want validation.
        """
        pass
    
    @abstractmethod
    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        """Format error into ViewModel."""
        pass

class PreferencesPresenter(ABC):
    """Abstract presenter for User Preferences and Credentials."""

    @abstractmethod
    def present_preferences(self, response: UserPreferencesResponse) -> PreferencesViewModel: 
        # Note: Return type can be 'PreferencesViewModel' if you create one, 
        # or just 'Dict' if mapping directly to JSON.
        pass

    @abstractmethod
    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        pass
    

class FreeSearchPresenter():
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
        pass
    
class AgentStatePresenter(ABC):
    @abstractmethod
    def present_state(self, agent_state: AgentState) -> AgentStateViewModel:
        pass

    @abstractmethod
    def present_message(self, message: str, agent_state: AgentState) -> AgentStateMessageViewModel:
        pass

    @abstractmethod
    def present_error(self, message: str, error_code: Optional[str] = None) -> ErrorViewModel:
        pass