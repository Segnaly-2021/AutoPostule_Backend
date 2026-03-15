from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.infrastructures.persistence.database.repositories.user_repo_db import UserRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.auth_repo_db import AuthRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.job_offer_repo_db import JobOfferRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.job_search_repo_db import JobSearchRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.subscription_repo_db import SubscriptionRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.user_preferences_repo_db import UserPreferencesRepoDB
from auto_apply_app.infrastructures.persistence.database.repositories.board_credentials_repo_db import BoardCredentialRepoDB


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def __aenter__(self):
        self.session = self.session_factory()
        
        # ✅ Standardized names to perfectly match InMemoryUnitOfWork
        self.user_repo = UserRepoDB(self.session)
        self.auth_repo = AuthRepoDB(self.session)
        self.subscription_repo = SubscriptionRepoDB(self.session)
        self.job_repo = JobOfferRepoDB(self.session)       # Renamed from job_offer_repo
        self.search_repo = JobSearchRepoDB(self.session)   # Renamed from job_search_repo
        self.user_pref_repo = UserPreferencesRepoDB(self.session)
        self.board_cred_repo = BoardCredentialRepoDB(self.session)
        
        # ✅ Return self for the 'async with ... as uow' context manager
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        # We handle rollbacks in the UoW base class or Use Cases, but let's ensure cleanup
        if exc_type is not None:
            await self.rollback()
        await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()