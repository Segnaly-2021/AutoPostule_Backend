from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager
import os

from auto_apply_app.infrastructures.persistence.database.models.schema import Base

# Define the database URL (fetch from environment variables)
DATABASE_URL = os.getenv("DATABASE_URL")

# 1. Create the engine
engine = create_async_engine(DATABASE_URL)

# 2. Create the Session factory
async_session = async_sessionmaker(
    class_=AsyncSession, 
    autocommit=False, 
    autoflush=False, 
    bind=engine
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_db_session():
    """Provides a transactional scope around a series of operations."""
    try:
        async with async_session() as session:
            yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

