from dotenv import load_dotenv
from enum import Enum
import os

# --- LangGraph Imports ---
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool


# Load environment variables from .env file
load_dotenv()


# Repository types
class RepositoryType(Enum):
    MEMORY = "memory"
    DATABASE = "database"


class Config:
    """Application configuration."""

    # Default values
    DEFAULT_REPOSITORY_TYPE: RepositoryType = RepositoryType.MEMORY


    # 🚨 SINGLETON POOL: Prevents creating thousands of connections
    _langgraph_db_pool = None
    
    @classmethod
    def get_repository_type(cls) -> RepositoryType:
        """Get the configured repository type."""
        repo_type_str = os.getenv("REPOSITORY_TYPE", cls.DEFAULT_REPOSITORY_TYPE.value)
        try:
            return RepositoryType(repo_type_str.lower())
        except ValueError:
            raise ValueError(f"Invalid repository type: {repo_type_str}")
        
    @classmethod
    def get_database_url(cls) -> str:
        """Get the database connection string."""
        url = os.getenv("DATABASE_DIRECT_URL")
        if not url and cls.get_repository_type() == RepositoryType.DATABASE:
            raise ValueError("DATABASE_DIRECT_URL is required when RepositoryType is DATABASE")
        return url

    @classmethod
    async def get_checkpointer(cls): # 🚨 Changed to async def
        repo_type = cls.get_repository_type()

        if repo_type == RepositoryType.MEMORY:
            from langgraph.checkpoint.memory import AsyncMemorySaver
            return AsyncMemorySaver()

        elif repo_type == RepositoryType.DATABASE:
            db_url = cls.get_database_url().replace("+asyncpg", "")
            
            if cls._langgraph_db_pool is None:
                # 🚨 Because this is now inside an async def, it will bind to the 
                # running asyncio event loop perfectly!
                cls._langgraph_db_pool = AsyncConnectionPool(
                    conninfo=db_url,
                    max_size=5,
                    num_workers=1,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": None,
                    }
                )
            
            return AsyncPostgresSaver(cls._langgraph_db_pool)

        raise ValueError(f"No checkpointer implementation for {repo_type}")

    @classmethod
    def get_gemini_key(cls) -> str:
        try:
            key = os.getenv("GEMINI_API_KEY")
            return key
        except ValueError as e:
            raise ValueError(f"Error GEMINI KEY: {e}")
        


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
                "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )
        return key