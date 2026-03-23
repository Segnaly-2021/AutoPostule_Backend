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




class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def __aenter__(self):
        self.session = self.session_factory()
        await self.session.begin()  # ← explicitly start ONE transaction here
        
        self.user_repo = UserRepoDB(self.session)
        self.auth_repo = AuthRepoDB(self.session)
        self.subscription_repo = SubscriptionRepoDB(self.session)
        self.job_repo = JobOfferRepoDB(self.session)
        self.search_repo = JobSearchRepoDB(self.session)
        self.user_pref_repo = UserPreferencesRepoDB(self.session)
        self.board_cred_repo = BoardCredentialRepoDB(self.session)
        self.agent_state_repo = AgentStateRepoDB(self.session)
        
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            await self.rollback()
        else:
            await self.commit()  # ← auto-commit on clean exit
        await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()