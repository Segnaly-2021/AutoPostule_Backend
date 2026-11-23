# ==============================================================================
# FILE: auto_apply_app/infrastructures/repository_factory.py
#
# WHAT IT DOES:
#   Chooses the storage backend from REPOSITORY_TYPE. MEMORY returns in-process fakes;
#   DATABASE returns SQLAlchemy Units of Work plus a Redis client. Its result is the
#   data-access foundation that the dependency-injection container is built on.
#
# USED BY:
#   - auto_apply_app/infrastructures/configuration/container.py: create_repositories()
#
# EXTERNAL DEPENDENCIES:
#   - redis: async client for the token blacklist, rate limiter and progress broker
#   - PostgreSQL (Supabase) through SQLAlchemy: via SqlAlchemyUnitOfWork
# ==============================================================================
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
# NOTE: possible issue: session.py raises at import when DATABASE_URL is unset, and this
# import is unconditional, so even MEMORY mode needs DATABASE_URL to be defined.
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
        # from_url does not connect yet; the first command does. decode_responses=True
        # makes Redis return str instead of bytes, which the broker relies on.
        redis_client = Redis.from_url(redis_url, decode_responses=True)
        token_repo = RedisTokenBlacklistRepository(redis_client)

        return (
            token_repo,
            # A factory, so each `async with uow_factory()` gets its own session and
            # transaction. Sessions must never be shared between concurrent tasks.
            lambda: SqlAlchemyUnitOfWork(session_factory=async_session),
            redis_client,
        )

    else:
        raise ValueError(f"Repository type: {repo_type} not supported")