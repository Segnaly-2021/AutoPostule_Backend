import os
import logging
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from auto_apply_app.infrastructures.persistence.database.models.schema import Base

logger = logging.getLogger(__name__)

# --- Database URL setup ---
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

# Auto-convert standard postgres URL to async driver URL
DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
# Translate 'sslmode' to 'ssl' so asyncpg doesn't crash
DATABASE_URL = DATABASE_URL.replace("sslmode=require", "ssl=require")

# --- Engine ---
# 🚨 PgBouncer compatibility:
# - statement_cache_size=0 + prepared_statement_cache_size=0 prevent the
#   "DuplicatePreparedStatementError" that occurs when asyncpg's prepared
#   statement cache collides with PgBouncer's connection multiplexing
#   in transaction/statement pool modes.
# - NullPool disables SQLAlchemy's own pooling so PgBouncer is the sole
#   pool manager. Double pooling causes connection state leaks.
engine = create_async_engine(
    DATABASE_URL,
    poolclass=NullPool,
    connect_args={
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    },
    echo=False,  # set to True in dev if you want SQL logged
)

# --- Session factory ---
async_session = async_sessionmaker(
    class_=AsyncSession,
    bind=engine,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_db_session():
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.exception(f"DB session error — rolling back: {type(e).__name__}")
            raise