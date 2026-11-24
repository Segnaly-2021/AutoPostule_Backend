# ==============================================================================
# FILE: auto_apply_app/application/repositories/unit_of_work.py
#
# WHAT IT DOES:
#   Defines the Unit of Work: one database transaction shared by all repositories.
#   Use cases open it with `async with uow_factory() as uow`; everything inside
#   commits or rolls back together. The concrete class lives in infrastructure.
#
# USED BY:
#   - auto_apply_app/application/use_cases/agent_use_cases.py: agent transactions
#   - auto_apply_app/application/use_cases/agent_state_use_cases.py: kill switch and heartbeat
#   - auto_apply_app/application/use_cases/agent_usage_use_cases.py: run completion
#   - auto_apply_app/application/use_cases/subscription_use_cases.py: billing transactions
#   - auto_apply_app/application/use_cases/job_offer_use_cases.py: application updates
#   - auto_apply_app/application/use_cases/fingerprint_use_cases.py: persona rotation
#   - auto_apply_app/application/use_cases/run_context_use_cases.py: loads run data
#   - auto_apply_app/application/use_cases/admin_use_cases.py: dashboard reads
#   - ... and 9 more
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
# auto_apply_app/application/repositories/unit_of_work.py
from abc import ABC, abstractmethod
from typing import Callable
from auto_apply_app.application.repositories.user_repo import UserRepository
from auto_apply_app.application.repositories.auth_repo import AuthRepository
from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.application.repositories.board_credentials_repo import BoardCredentialsRepository
from auto_apply_app.application.repositories.preferences_repo import UserPreferencesRepository
from auto_apply_app.application.repositories.user_fingerprint_repo import UserFingerprintRepository
from auto_apply_app.application.repositories.agent_state_repo import AgentStateRepository
from auto_apply_app.application.repositories.agent_usage_repo import AgentUsageRepository
from auto_apply_app.application.repositories.free_search_usage_repo import FreeSearchUsageRepository
from auto_apply_app.application.repositories.page_view_repo import PageViewRepository
from auto_apply_app.application.repositories.credit_transaction_repo import CreditTransactionRepository
from auto_apply_app.application.repositories.message_log_repo import MessageLogRepository
from auto_apply_app.application.repositories.announcement_repo import AnnouncementRepository


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
    user_fingerprint_repo: UserFingerprintRepository
    agent_state_repo: AgentStateRepository
    agent_usage_repo: AgentUsageRepository
    free_search_usage_repo: FreeSearchUsageRepository
    page_view_repo: PageViewRepository
    credit_tx_repo: CreditTransactionRepository
    message_log_repo: MessageLogRepository
    announcement_repo: AnnouncementRepository

    async def __aenter__(self):
        return self

    # Important: a `return` inside the `async with` block is a normal exit, so it
    # COMMITS. Use cases rely on this to save failed-attempt counters while
    # returning Result.failure. Only a raised exception rolls back.
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


UnitOfWorkFactory = Callable[[], "UnitOfWork"]