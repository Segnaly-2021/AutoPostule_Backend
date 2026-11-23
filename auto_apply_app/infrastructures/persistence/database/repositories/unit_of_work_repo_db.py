# ==============================================================================
# FILE: auto_apply_app/infrastructures/persistence/database/repositories/unit_of_work_repo_db.py
#
# WHAT IT DOES:
#   The SQLAlchemy Unit of Work (UoW): one database transaction shared by all
#   repositories. A use case writes `async with uow_factory() as uow:`, works through
#   uow.user_repo, uow.job_repo and so on, and everything commits together on a clean
#   exit or rolls back together on an exception.
#
# USED BY:
#   - auto_apply_app/infrastructures/repository_factory.py: creates one per uow_factory() call
#
# EXTERNAL DEPENDENCIES:
#   - SQLAlchemy: async sessions and transactions
#   - PostgreSQL (Supabase): the transaction target
# ==============================================================================
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.infrastructures.persistence.database.repositories.user_repo_db import UserRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.auth_repo_db import AuthRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.job_offer_repo_db import JobOfferRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.job_search_repo_db import JobSearchRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.subscription_repo_db import SubscriptionRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.user_preferences_repo_db import UserPreferencesRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.board_credentials_repo_db import BoardCredentialRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.agent_state_repo_db import AgentStateRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.user_fingerprint_repo_db import UserFingerprintRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.agent_usage_repo_db import AgentUsageRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.free_search_usage_repo_db import FreeSearchUsageRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.page_view_repo_db import PageViewRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.credit_transaction_repo_db import CreditTransactionRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.message_log_repo_db import MessageLogRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.announcement_repo_db import AnnouncementRepoDB


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def __aenter__(self):
        self.session = self.session_factory()
        await self.session.begin()

        # All repositories share this one session, which is what makes their writes
        # part of the same transaction.
        
        self.user_repo = UserRepoDB(self.session)
        self.auth_repo = AuthRepoDB(self.session)
        self.subscription_repo = SubscriptionRepoDB(self.session)
        self.job_repo = JobOfferRepoDB(self.session)
        self.search_repo = JobSearchRepoDB(self.session)
        self.user_pref_repo = UserPreferencesRepoDB(self.session)
        self.board_cred_repo = BoardCredentialRepoDB(self.session)
        self.agent_state_repo = AgentStateRepoDB(self.session)
        self.user_fingerprint_repo = UserFingerprintRepoDB(self.session)
        
        # NEW
        self.agent_usage_repo = AgentUsageRepoDB(self.session)
        self.free_search_usage_repo = FreeSearchUsageRepoDB(self.session)

        # Observability
        self.page_view_repo = PageViewRepoDB(self.session)
        self.credit_tx_repo = CreditTransactionRepoDB(self.session)
        self.message_log_repo = MessageLogRepoDB(self.session)
        self.announcement_repo = AnnouncementRepoDB(self.session)

        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        # NOTE: possible issue: close() is not in a `finally`. If commit() or rollback()
        # raises (for example on a dropped connection), the session is not closed here.
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()  # ← auto-commit on clean exit
        await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()