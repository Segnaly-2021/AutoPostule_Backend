from abc import ABC, abstractmethod
from auto_apply_app.application.repositories.user_repo import UserRepository
from auto_apply_app.application.repositories.auth_repo import AuthRepository
from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.application.repositories.board_credentials_repo import BoardCredentialsRepository
from auto_apply_app.application.repositories.preferences_repo import UserPreferencesRepository



class UnitOfWork(ABC):
    """
    Abstract Context Manager for atomic operations.
    When we enter this context, we expect all repository operations
    to share the same database session.
    """
    user_repo: UserRepository
    auth_repo: AuthRepository
    subscription_repo: SubscriptionRepository
    job_repo: JobOfferRepository
    search_repo: JobSearchRepository
    user_pref_repo: UserPreferencesRepository
    board_cred_repo: BoardCredentialsRepository
    
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()

    @abstractmethod
    async def commit(self):
        pass

    @abstractmethod
    async def rollback(self):
        pass