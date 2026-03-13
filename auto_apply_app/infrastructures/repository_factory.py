from typing import Tuple

from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.application.repositories.user_repo import UserRepository
from auto_apply_app.application.repositories.auth_repo import AuthRepository
from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork 
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.infrastructures.persistence.in_memory.memory import (
  InMemoryTokenBlacklistRepository,
  InMemoryUnitOfWork
)
from auto_apply_app.infrastructures.config import Config, RepositoryType


def create_repositories() -> Tuple[
    UserRepository, 
    JobOfferRepository, 
    JobSearchRepository,
    AuthRepository,
    SubscriptionRepository,
    TokenBlacklistRepository,
    UnitOfWork
]:
    
    repo_type = Config.get_repository_type() 
    # users = {}
    # auth_storage = {}
    # sub_storage = {}

    if repo_type == RepositoryType.MEMORY:

        # user_repo = InMemoryUserRepository(users)
        # job_repo = InMemoryJobOfferRepository()
        # search_repo = InMemoryJobSearchRepository()
        # auth_repo = InMemoryAuthRepository(auth_storage)
        # sub_repo = InMemorySubscriptionRepository(sub_storage)
        token_repo = InMemoryTokenBlacklistRepository()
        uow = InMemoryUnitOfWork()

        #search_repo.set_job_repository(job_repo)
        return token_repo, uow #user_repo, job_repo, search_repo, auth_repo, sub_repo,  
    
    elif repo_type == RepositoryType.DATABASE:
        pass
    else:
        raise ValueError(f"Repository type: {repo_type} not supported")
