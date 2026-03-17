import os
from typing import Tuple
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


def create_repositories() -> Tuple[TokenBlacklistRepository, UnitOfWork]:
    """
    Factory to create and return the core data access components based on configuration.
    Returns a tuple of (TokenBlacklistRepository, UnitOfWork).
    """
    repo_type = Config.get_repository_type() 

    if repo_type == RepositoryType.MEMORY:
        # --- DEVELOPMENT / TESTING ---
        token_repo = InMemoryTokenBlacklistRepository()
        uow = InMemoryUnitOfWork()
        return token_repo, uow  
    
    elif repo_type == RepositoryType.DATABASE:
        # --- PRODUCTION ---
        
        # 1. Setup Redis Token Blacklist
        # Fallback to localhost if not set (useful for local database testing)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        token_repo = RedisTokenBlacklistRepository(redis_client)

        # 2. Setup SQLAlchemy Unit of Work
        # We pass the async_sessionmaker imported from our session.py
        uow = SqlAlchemyUnitOfWork(session_factory=async_session)

        return token_repo, uow

    else:
        raise ValueError(f"Repository type: {repo_type} not supported")