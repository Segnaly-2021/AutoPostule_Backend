# auto_apply_app/application/service_ports/agent_port.py
from uuid import UUID
from abc import ABC, abstractmethod
from typing import Callable, Optional

from auto_apply_app.domain.entities.user import User
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.domain.entities.board_credentials import BoardCredential
from auto_apply_app.domain.entities.user_preferences import UserPreferences

class AgentServicePort(ABC):
    """
    Interface for the Autonomous Agent.
    The Infrastructure layer (LangGraph) must implement this.
    """
    
    @abstractmethod
    async def run_job_search(
        self, 
        user: User, 
        search: JobSearch,
        subscription: UserSubscription,
        preferences: UserPreferences,
        credentials: Optional[BoardCredential] = None,
        progress_callback: Optional[Callable] = None
    ) -> None:
        """
        Triggers the agent workflow from the start.
        
        Args:
            user: The full user entity (contains resume, credentials).
            search: The active search entity (contains the 'To-Do' list).
            subscription: User's subscription (for routing logic).
            preferences: User's preferences (automation mode, active boards, creativity).
            progress_callback: Optional async function to send progress updates.
        """
        pass
    
    @abstractmethod
    async def resume_job_search(
        self,
        user: User,
        search: JobSearch,
        progress_callback: Optional[Callable] = None
    ) -> None:
        """
        Resumes a paused workflow from a checkpoint.
        Used when Premium users approve drafts.
        
        Args:
            user: The full user entity.
            search: The job search to resume.
            subscription: User's subscription.
            preferences: User's preferences 
            progress_callback: Optional async function to send progress updates.
        """
        pass
    
    @abstractmethod
    async def kill_job_search(self, search_id: UUID) -> None:
        """
        Immediately terminates a running job search.
        Closes browser, cleans up resources, marks search as cancelled.
        
        Args:
            search_id: UUID of the job search to kill.
            
        Note:
            This is a destructive operation. Any unsaved progress will be lost.
            The browser session will be forcefully closed.
        """
        pass