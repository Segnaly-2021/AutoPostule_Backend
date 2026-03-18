from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from contextlib import asynccontextmanager
import os

from auto_apply_app.infrastructures.persistence.database.models.schema import Base

# Define the database URL (fetch from environment variables)
DATABASE_URL = os.getenv("DATABASE_URL")

# 🚨 FIX: Auto-convert standard postgres URL to the async driver URL
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
    # 2. Translate 'sslmode' to 'ssl' so asyncpg doesn't crash
    DATABASE_URL = DATABASE_URL.replace("sslmode=require", "ssl=require")

# 1. Create the engine
engine = create_async_engine(
    DATABASE_URL,
    # just for testing
    connect_args={
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0
    }
        
)

# 2. Create the Session factory
async_session = async_sessionmaker(
    class_=AsyncSession, 
    bind=engine,
    expire_on_commit=False
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
        except Exception:
            await session.rollback()
            raise

