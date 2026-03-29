from dotenv import load_dotenv
from enum import Enum
from typing import Any
import pickle
import os

# --- LangGraph Imports ---
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

load_dotenv()


# ============================================================================
# CUSTOM SERIALIZER
# ============================================================================

class PickleSerde:
    """
    A custom serializer that preserves rich Python objects
    (Dataclasses, Enums, UUIDs) so LangGraph doesn't flatten
    them into plain dictionaries when checkpointing state.
    """
    def dumps(self, obj: Any) -> bytes:
        return pickle.dumps(obj)

    def loads(self, data: bytes) -> Any:
        return pickle.loads(data)


# ============================================================================
# REPOSITORY TYPE
# ============================================================================

class RepositoryType(Enum):
    MEMORY = "memory"
    DATABASE = "database"


# ============================================================================
# CONFIG
# ============================================================================

class Config:
    """Application configuration."""

    DEFAULT_REPOSITORY_TYPE: RepositoryType = RepositoryType.MEMORY

    # Singleton pool — prevents creating thousands of connections
    _langgraph_db_pool = None

    @classmethod
    def get_repository_type(cls) -> RepositoryType:
        repo_type_str = os.getenv("REPOSITORY_TYPE", cls.DEFAULT_REPOSITORY_TYPE.value)
        try:
            return RepositoryType(repo_type_str.lower())
        except ValueError:
            raise ValueError(f"Invalid repository type: {repo_type_str}")

    @classmethod
    def get_database_url(cls) -> str:
        url = os.getenv("DATABASE_URL")
        if not url and cls.get_repository_type() == RepositoryType.DATABASE:
            raise ValueError("DATABASE_URL is required when RepositoryType is DATABASE")
        return url

    @classmethod
    async def get_checkpointer(cls):
        repo_type = cls.get_repository_type()

        if repo_type == RepositoryType.MEMORY:
            from langgraph.checkpoint.memory import AsyncMemorySaver
            return AsyncMemorySaver(serde=PickleSerde())

        elif repo_type == RepositoryType.DATABASE:
            db_url = cls.get_database_url().replace("+asyncpg", "")

            if cls._langgraph_db_pool is None:
                cls._langgraph_db_pool = AsyncConnectionPool(
                    conninfo=db_url,
                    max_size=5,
                    num_workers=1,
                    open=False,
                    check=AsyncConnectionPool.check_connection,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": None,
                        "keepalives": 1,
                        "keepalives_idle": 60,
                        "keepalives_interval": 10,
                        "keepalives_count": 5,
                    }
                )

            await cls._langgraph_db_pool.open()

            checkpointer = AsyncPostgresSaver(
                cls._langgraph_db_pool,
                serde=PickleSerde()  # ← preserves Enums, UUIDs, Dataclasses
            )

            print("🛠️ [Config] Ensuring LangGraph checkpoint tables exist in Supabase...")
            await checkpointer.setup()

            return checkpointer

        raise ValueError(f"No checkpointer implementation for {repo_type}")

    @classmethod
    def get_gemini_key(cls) -> str:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY not found in environment variables.")
        return key

    @classmethod
    def get_encryption_key(cls) -> str:
        """
        Get the encryption key for credential storage.

        SECURITY NOTE: This key MUST be:
        1. Set in environment variables (never hardcoded)
        2. The same across deployments (or old credentials can't be decrypted)
        3. Kept secret (if compromised, all credentials are exposed)
        """
        key = os.getenv("ENCRYPTION_KEY")
        if not key:
            raise ValueError(
                "ENCRYPTION_KEY not found in environment variables. "
                "Generate one with: python -c "
                "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        return key