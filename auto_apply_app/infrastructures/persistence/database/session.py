# ==============================================================================
# FILE: auto_apply_app/infrastructures/persistence/database/session.py
#
# WHAT IT DOES:
#   Creates the one async SQLAlchemy engine and the session factory for the app.
#   It is tuned for Supabase's connection pooler (Supavisor, port 6543): SQLAlchemy
#   keeps no pool of its own (NullPool), and prepared-statement caching is off.
#   Each Unit of Work therefore opens a connection for one short transaction.
#   Importing this file raises immediately if DATABASE_URL is missing.
#
# USED BY:
#   - auto_apply_app/infrastructures/repository_factory.py: passes async_session to the UoW
#   - auto_apply_app/infrastructures/api/app.py: disposes the engine at shutdown
#   - scripts/preflight.py: pre-deployment checks
#
# EXTERNAL DEPENDENCIES:
#   - SQLAlchemy: async engine, sessions and NullPool
#   - asyncpg: the PostgreSQL driver selected by the URL rewrite below
#   - Supabase (Postgres + Supavisor pooler): the database server
# ==============================================================================
import os
import logging
from uuid import uuid4
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
#
# Why this matters here: in transaction mode the pooler can give each transaction a
# different real server connection, so anything tied to one connection, like a named
# prepared statement, may be missing or already exist on the next one. The random
# statement name below avoids those name collisions.
# NullPool opens and closes a connection for each checkout. The project keeps this on
# purpose for free-tier Supabase; do not add SQLAlchemy pooling without reviewing that.
engine = create_async_engine(
    DATABASE_URL,
    poolclass=NullPool,
    connect_args={
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4().hex}__",  # ADD
    },
    echo=False,  # set to True in dev if you want SQL logged
)

# --- Session factory ---
# expire_on_commit=False keeps loaded attributes readable after commit. With async
# SQLAlchemy, an expired attribute would trigger an implicit query and raise an error.
async_session = async_sessionmaker(
    class_=AsyncSession,
    bind=engine,
    expire_on_commit=False,
)


# Creates missing tables directly from the models. Nothing calls this today (its import
# in app.py is commented out); the schema is managed through Alembic (alembic/env.py).
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