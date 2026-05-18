import os
from typing import Tuple, Callable, Optional
from redis.asyncio import Redis

# Interfaces
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork

# InMemory Implementations
from auto_apply_app.infrastructures.persistence.in_memory.memory import (
    InMemoryTokenBlacklistRepository,
    InMemoryUnitOfWork,
)

# Database/Production Implementations
from auto_apply_app.infrastructures.persistence.database.repositories.unit_of_work_repo_db import SqlAlchemyUnitOfWork
from auto_apply_app.infrastructures.persistence.database.session import async_session
from auto_apply_app.infrastructures.persistence.database.repositories.token_blacklist_repo_db import RedisTokenBlacklistRepository

from auto_apply_app.infrastructures.config import Config, RepositoryType


def create_repositories() -> Tuple[TokenBlacklistRepository, Callable[[], UnitOfWork], Optional[Redis]]:
    """
    Factory to create and return the core data access components based on configuration.

    Returns a tuple of:
      - TokenBlacklistRepository
      - UoW Factory (callable -> UnitOfWork)
      - Redis client (None in MEMORY mode, live client in DATABASE mode)

    The Redis client is surfaced here so other adapters (e.g. rate limiters)
    can reuse the same connection pool instead of opening a second one.
    """
    repo_type = Config.get_repository_type()

    if repo_type == RepositoryType.MEMORY:
        token_repo = InMemoryTokenBlacklistRepository()
        return token_repo, lambda: InMemoryUnitOfWork(), None

    elif repo_type == RepositoryType.DATABASE:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        token_repo = RedisTokenBlacklistRepository(redis_client)

        return (
            token_repo,
            lambda: SqlAlchemyUnitOfWork(session_factory=async_session),
            redis_client,
        )

    else:
        raise ValueError(f"Repository type: {repo_type} not supported")