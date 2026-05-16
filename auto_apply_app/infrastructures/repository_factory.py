import os
from typing import Tuple, Callable
from redis.asyncio import Redis

# Interfaces
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork 

# InMemory Implementations
from auto_apply_app.infrastructures.persistence.in_memory.memory import (
    InMemoryTokenBlacklistRepository,
    InMemoryUnitOfWork
)

# Database/Production Implementations
from auto_apply_app.infrastructures.persistence.database.repositories.unit_of_work_repo_db import SqlAlchemyUnitOfWork
from auto_apply_app.infrastructures.persistence.database.session import async_session
from auto_apply_app.infrastructures.persistence.database.repositories.token_blacklist_repo_db import RedisTokenBlacklistRepository

from auto_apply_app.infrastructures.config import Config, RepositoryType



def create_repositories() -> Tuple[TokenBlacklistRepository, Callable[[], UnitOfWork]]:
    """
    Factory to create and return the core data access components based on configuration.
    Returns a tuple of (TokenBlacklistRepository, UoW Factory).
    """
    repo_type = Config.get_repository_type() 

    if repo_type == RepositoryType.MEMORY:
        token_repo = InMemoryTokenBlacklistRepository()
        # 🚨 Return a lambda function that creates a fresh UoW
        return token_repo, lambda: InMemoryUnitOfWork()  
    
    elif repo_type == RepositoryType.DATABASE:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        token_repo = RedisTokenBlacklistRepository(redis_client)

        # 🚨 Return a lambda function that creates a fresh UoW for every request
        return token_repo, lambda: SqlAlchemyUnitOfWork(session_factory=async_session)

    else:
        raise ValueError(f"Repository type: {repo_type} not supported")